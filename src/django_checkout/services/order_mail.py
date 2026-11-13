# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""When an order is sent its confirmation and cancellation e-mails, and the staff resend.

The unique (order, kind) row of ``OrderMail`` is the only guard against a second e-mail: every hook may call
``schedule_*`` again, ``get_or_create`` makes every later call a no-op. django_email is imported only by the task.
"""

import logging
from collections.abc import Callable
from functools import partial

from django.apps import apps
from django.db import transaction
from django.db.models import Q

from django_checkout import settings as checkout_settings
from django_checkout.domain.payment_selection import settling_payment_method
from django_checkout.enums import ONLINE_GATEWAY_PROVIDERS, OrderMailKind, OrderMailStatus, OrderStatus, PaymentProvider
from django_checkout.models import Order, OrderMail, PaymentMethod
from django_checkout.schemas.responses.order import OrderMailResponse
from django_checkout.services.exceptions import ClientError
from django_checkout.services.order_mail_group import lead_order, purchase_orders
from django_checkout.utils import payment_entries

logger = logging.getLogger("process")

PAID_STATUSES = frozenset({OrderStatus.CONFIRMED, OrderStatus.HOLDED, OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED})
LIVE_CONFIRMATION_STATUSES = (OrderMailStatus.SENT, OrderMailStatus.PENDING)


class PaymentKind:
    TRANSFER = "transfer"
    COD = "cod"
    ONLINE = "online"
    OTHER = "other"


class OrderMailRefused(ClientError):
    """A resend the order cannot take (view -> 400)."""


def settling_method(order: Order) -> PaymentMethod | None:
    """The method that settles the order, resolved from the body's payment entries (one query)."""
    codes = [entry["code"] for entry in payment_entries(order.order_body) if entry.get("code")]
    if not codes:
        return None
    rows = {row.code: row for row in PaymentMethod.objects.filter(channel_id=order.channel_id, code__in=codes)}
    return settling_payment_method([rows.get(code) for code in codes])


def payment_kind(method: PaymentMethod | None) -> str:
    provider = getattr(method, "provider", None)
    if provider == PaymentProvider.TRANSFER:
        return PaymentKind.TRANSFER
    if getattr(method, "is_cash_on_delivery", False):
        return PaymentKind.COD
    return PaymentKind.ONLINE if provider in ONLINE_GATEWAY_PROVIDERS else PaymentKind.OTHER


def is_due(order: Order) -> bool:
    """Confirmed, or settled by anything but an online gateway; a canceled order never."""
    if order.order_status == OrderStatus.CANCELED:
        return False
    if order.order_status == OrderStatus.CONFIRMED:
        return True
    return payment_kind(settling_method(order)) != PaymentKind.ONLINE


def schedule_confirmation(order: Order) -> None:
    if not _enabled(checkout_settings.CHECKOUT_ORDER_CONFIRMATION_EMAIL):
        return
    lead = lead_order(order)
    if not is_due(lead):
        return
    row, created = OrderMail.objects.get_or_create(order=lead, kind=OrderMailKind.CONFIRMATION)
    if created:
        _enqueue(row)


def schedule_canceled(order: Order, was_paid: bool) -> None:
    """Only an order whose purchase got (or is getting) its confirmation is told it was canceled."""
    if not _enabled(checkout_settings.CHECKOUT_ORDER_CANCELED_EMAIL):
        return
    confirmation = OrderMail.objects.filter(order=lead_order(order), kind=OrderMailKind.CONFIRMATION)
    if not confirmation.filter(status__in=LIVE_CONFIRMATION_STATUSES).exists():
        return
    row, created = OrderMail.objects.get_or_create(order=order, kind=OrderMailKind.CANCELED)
    if created:
        _enqueue(row, was_paid)


def resend(order: Order, kind: str, user) -> OrderMail:  # noqa: ANN001 - the staff user
    """The row of that kind back to ``pending`` and enqueued; a cancellation e-mail is never created here."""
    target = lead_order(order) if kind == OrderMailKind.CONFIRMATION else order
    if not apps.is_installed("django_email"):
        raise OrderMailRefused("Order e-mails are not available.")
    if not (target.billing_email or target.shipping_email):
        raise OrderMailRefused("The order has no e-mail address.")
    with transaction.atomic():
        row = _resend_row(target, kind)
        row.status, row.requested_by = OrderMailStatus.PENDING, user
        row.save(update_fields=["status", "requested_by", "updated"])
    _enqueue(row)
    return row


def _resend_row(order: Order, kind: str) -> OrderMail:
    if kind == OrderMailKind.CONFIRMATION:
        return OrderMail.objects.select_for_update().get_or_create(order=order, kind=kind)[0]
    row = OrderMail.objects.select_for_update().filter(order=order, kind=kind).first()
    if row is None:
        raise OrderMailRefused("The order was never sent a cancellation e-mail.")
    return row


# --- hooks: registered by Order.create, the split worker and Order.save; they never reach the order flow ---


def schedule_confirmation_after_commit(order_pk: int) -> None:
    _after_commit(partial(schedule_confirmation_by_pk, order_pk))


def schedule_canceled_after_commit(order_pk: int, paid_before: bool) -> None:
    """``paid_before``: the status before the cancellation was a paid one."""
    _after_commit(partial(schedule_canceled_by_pk, order_pk, paid_before))


def schedule_confirmation_by_pk(order_pk: int) -> None:
    _guarded(order_pk, schedule_confirmation)


def schedule_canceled_by_pk(order_pk: int, paid_before: bool) -> None:
    def schedule(order: Order) -> None:
        schedule_canceled(order, paid_before and takes_payment_upfront(settling_method(order)))

    _guarded(order_pk, schedule)


def takes_payment_upfront(method: PaymentMethod | None) -> bool:
    """Money moved before the order is shipped: not cash on delivery, not a free order."""
    return payment_kind(method) != PaymentKind.COD and not getattr(method, "is_free_order", False)


def _after_commit(callback: Callable[[], None]) -> None:
    try:
        transaction.on_commit(callback)
    except Exception:
        logger.exception("order mail: hook not registered")


def _guarded(order_pk: int, action: Callable[[Order], None]) -> None:
    """Reloads the order after commit, so the action sees its final status; logs any failure."""
    try:
        order = Order.objects.filter(pk=order_pk).select_related("channel").first()
        if order is not None:
            action(order)
    except Exception:
        logger.exception("order mail: scheduling failed for order %s", order_pk)


def _enabled(setting: object) -> bool:
    return bool(setting) and apps.is_installed("django_email")


def _enqueue(row: OrderMail, was_paid: bool | None = None) -> None:
    transaction.on_commit(partial(_deliver_later, row.pk, was_paid))


def _deliver_later(mail_id: int, was_paid: bool | None) -> None:
    """A broker that refuses the task leaves the row failed, not pending forever: staff see it and resend."""
    from django_checkout.worker.order_mail_tasks import send_order_mail

    try:
        send_order_mail.delay(mail_id, was_paid)
    except Exception as exc:
        logger.exception("order mail %s: not enqueued", mail_id)
        failed = {"status": OrderMailStatus.FAILED, "last_error": f"{type(exc).__name__}: not enqueued"}
        OrderMail.objects.filter(pk=mail_id, status=OrderMailStatus.PENDING).update(**failed)


# --- admin read ---


def mail_rows(order: Order) -> list[OrderMailResponse]:
    """The order's own rows plus, for a part of a split purchase, the lead's confirmation."""
    orders = purchase_orders(order)
    rows = OrderMail.objects.filter(Q(order=order) | Q(order=orders[0], kind=OrderMailKind.CONFIRMATION))
    numbers = [part.pretty_id or "" for part in orders]
    return [
        mail_response(row, numbers if row.kind == OrderMailKind.CONFIRMATION else [order.pretty_id or ""])
        for row in rows.order_by("created", "pk")
    ]


def purchase_numbers(order: Order) -> list[str]:
    return [part.pretty_id or "" for part in purchase_orders(order)]


def mail_response(row: OrderMail, order_numbers: list[str]) -> OrderMailResponse:
    return OrderMailResponse(
        kind=row.kind,
        status=row.status,
        recipient=row.recipient,
        language=row.language,
        attempts=row.attempts,
        last_error=row.last_error,
        sent_at=row.sent_at,
        created=row.created,
        order_numbers=order_numbers,
    )
