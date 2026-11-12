# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Send one order e-mail of the ``OrderMail`` log: claim, send by kind, retry ladder, final state.

django_email is imported inside the task only: the module is registered by the AppConfig without the extra.
"""

import logging
from smtplib import SMTPException

from celery import shared_task
from django.apps import apps
from django.db import transaction
from django.utils import timezone

from django_checkout.enums import OrderMailKind, OrderMailStatus, OrderStatus
from django_checkout.models import Order, OrderMail

logger = logging.getLogger("process")

RETRY_COUNTDOWNS = (60, 180, 420, 900, 1800)
RETRYABLE_ERRORS = (SMTPException, OSError)
CHANNEL_NOT_SET = "Channel is not set"  # django_email: the shop has no e-mail channel
NOTIFY_ROLE = "orders"
LAST_ERROR_LENGTH = 255


@shared_task(name="django_checkout.send_order_mail", bind=True, acks_late=True, max_retries=len(RETRY_COUNTDOWNS))
def send_order_mail(self, mail_id: int, was_paid: bool | None = None) -> None:  # noqa: ANN001 - bound task
    """``was_paid`` travels with a cancellation; a resend passes None and it is recomputed."""
    final_try = self.request.retries >= self.max_retries
    with transaction.atomic():
        row = (
            OrderMail.objects.select_for_update(of=("self",))
            .select_related("order__channel")
            .filter(pk=mail_id)
            .first()
        )
        if row is None or row.status != OrderMailStatus.PENDING:
            return
        retry_error = _attempt(row, was_paid, final_try)
    if retry_error is not None:
        raise self.retry(exc=retry_error, countdown=RETRY_COUNTDOWNS[self.request.retries])


def _attempt(row: OrderMail, was_paid: bool | None, final_try: bool) -> Exception | None:
    """Send under the row lock and record the outcome; returns the error to retry on, else None."""
    try:
        with transaction.atomic():  # a database error inside rolls back to here, the outcome can still be saved
            _send(row, was_paid)
    except RETRYABLE_ERRORS as exc:
        _record_error(row, exc, OrderMailStatus.PENDING if not final_try else OrderMailStatus.FAILED)
        if not final_try:
            return exc
        _notify_staff(row)
    except ValueError as exc:
        if str(exc) != CHANNEL_NOT_SET:
            _fail(row, exc)
        else:
            _finish(row, OrderMailStatus.SKIPPED)
    except Exception as exc:
        _fail(row, exc)
    return None


def _send(row: OrderMail, was_paid: bool | None) -> None:
    order = row.order
    recipient = order.billing_email or order.shipping_email
    if not recipient or _confirms_a_canceled_order(row):
        _finish(row, OrderMailStatus.SKIPPED)
        return
    language = mail_language(order)
    row.recipient, row.language, row.attempts = recipient, language, row.attempts + 1
    builder, service_class = _kinds()[row.kind]
    paid = was_paid if was_paid is not None else _was_paid(order)
    data = builder(order, language, paid)
    service_class(language=language, channel_idx=order.channel.idx).send([recipient], data)
    row.sent_at, row.last_error = timezone.now(), ""
    _finish(row, OrderMailStatus.SENT)


def _confirms_a_canceled_order(row: OrderMail) -> bool:
    """A confirmation still retrying when the order was canceled: the cancellation e-mail tells the customer."""
    return row.kind == OrderMailKind.CONFIRMATION and row.order.order_status == OrderStatus.CANCELED


def _kinds() -> dict:
    """kind -> (data builder, e-mail service)."""
    from django_email.service.checkout.order_canceled import OrderCanceledEmail
    from django_email.service.checkout.order_confirmation import OrderConfirmationEmail

    from django_checkout.services.order_mail_data import build_canceled_data, build_confirmation_data

    return {
        OrderMailKind.CONFIRMATION: (
            lambda order, language, _paid: build_confirmation_data(order, language),
            OrderConfirmationEmail,
        ),
        OrderMailKind.CANCELED: (
            lambda order, language, paid: build_canceled_data(order, paid, language),
            OrderCanceledEmail,
        ),
    }


def mail_language(order: Order) -> str:
    """``language_code`` of the body when the e-mails know it, else the checkout channel's default."""
    from django_email.language import resolve_email_language
    from django_email.settings import EMAIL_AVAILABLE_LANGUAGES, EMAIL_DEFAULT_LANGUAGE

    body = order.order_body if isinstance(order.order_body, dict) else {}
    requested = body.get("language_code")
    known = requested if requested in EMAIL_AVAILABLE_LANGUAGES else None
    return resolve_email_language(known, order.channel) or EMAIL_DEFAULT_LANGUAGE


def _was_paid(order: Order) -> bool:
    """A resent cancellation: paid when the order settled online and its confirmation went out."""
    from django_checkout.services.order_mail import PaymentKind, payment_kind, settling_method
    from django_checkout.services.order_mail_group import lead_order

    if payment_kind(settling_method(order)) != PaymentKind.ONLINE:
        return False
    confirmation = OrderMail.objects.filter(order=lead_order(order), kind=OrderMailKind.CONFIRMATION)
    return confirmation.filter(status=OrderMailStatus.SENT).exists()


def _fail(row: OrderMail, exc: Exception) -> None:
    """A broken template or data does not heal by itself: failed at once, no retry."""
    logger.exception("order mail %s (%s) of order %s failed", row.pk, row.kind, row.order_id)
    _record_error(row, exc, OrderMailStatus.FAILED)


def _record_error(row: OrderMail, exc: Exception, status: str) -> None:
    """The exception class and its message; an SMTP refusal quotes the address, which is masked."""
    message = str(exc).replace(row.recipient, "<recipient>") if row.recipient else str(exc)
    row.last_error = f"{type(exc).__name__}: {message}"[:LAST_ERROR_LENGTH]
    _finish(row, status)


def _finish(row: OrderMail, status: str) -> None:
    row.status = status
    row.save()


def _notify_staff(row: OrderMail) -> None:
    """One staff notification once the retries are spent; never raises."""
    if not apps.is_installed("django_notifications"):
        return
    from django_notifications.services.notify_service import notify

    order = row.order
    try:
        notify(
            channel_idx=order.channel.idx,
            recipient_role=NOTIFY_ROLE,
            severity="high",
            subject_ref=f"checkout.Order:{order.order_id}",
            title="Order e-mail not sent",
            body=f"The {row.kind} e-mail of order {order.pretty_id} was not sent after {row.attempts} attempts.",
        )
    except Exception:
        logger.exception("order mail %s: staff notification failed", row.pk)
