# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The send task: claim under a row lock, send by kind, retry ladder on SMTP errors, final states."""

import sys
import threading
import types
from smtplib import SMTPRecipientsRefused, SMTPServerDisconnected

import pytest
from django.core import mail
from django.db import connection

from django_checkout.enums import OrderMailKind, OrderMailStatus, OrderStatus
from django_checkout.models import Order, OrderMail
from django_checkout.worker import order_mail_tasks
from django_checkout.worker.order_mail_tasks import RETRY_COUNTDOWNS, send_order_mail
from tests.order_mail_support import BUYER, email_channel, make_order

__all__ = ["email_channel"]  # fixtures

pytestmark = pytest.mark.usefixtures("email_channel", "locmem_email")


class Retried(Exception):
    pass


@pytest.fixture
def locmem_email(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox = []


@pytest.fixture
def retries(monkeypatch) -> list[int]:
    """Records the countdown of each retry the task asks for instead of reaching a broker."""
    countdowns = []

    def retry(exc=None, countdown=None):
        countdowns.append(countdown)
        return Retried()

    monkeypatch.setattr(send_order_mail, "retry", retry)
    return countdowns


def _run(mail_id: int, retries: int = 0, was_paid: bool | None = None) -> None:
    send_order_mail.push_request(retries=retries)
    try:
        send_order_mail.run(mail_id, was_paid)
    finally:
        send_order_mail.pop_request()


def _row(order: Order, kind: str = OrderMailKind.CONFIRMATION) -> OrderMail:
    return OrderMail.objects.create(order=order, kind=kind)


def _smtp_down(monkeypatch):
    def send(self, *args, **kwargs):
        raise SMTPServerDisconnected("connection lost")

    monkeypatch.setattr("django_email.domain.EmailDomain.send_email", send)


@pytest.mark.django_db
class TestSend:
    def test_success_sends_the_confirmation_and_records_it(self, channel):
        row = _row(make_order(channel, "transfer"))

        _run(row.pk)

        row.refresh_from_db()
        assert (row.status, row.recipient, row.language, row.attempts) == (OrderMailStatus.SENT, BUYER, "en", 1)
        assert row.sent_at is not None
        assert len(mail.outbox) == 1 and mail.outbox[0].to == [BUYER]
        assert row.order.pretty_id in mail.outbox[0].subject

    def test_cancellation_is_sent_by_its_own_service(self, channel):
        order = make_order(channel, "transfer", status=OrderStatus.CANCELED)

        _run(_row(order, OrderMailKind.CANCELED).pk, was_paid=False)

        assert len(mail.outbox) == 1 and order.pretty_id in mail.outbox[0].subject

    def test_row_already_sent_sends_nothing(self, channel):
        row = _row(make_order(channel))
        OrderMail.objects.filter(pk=row.pk).update(status=OrderMailStatus.SENT)

        _run(row.pk)

        assert mail.outbox == []

    def test_order_without_an_address_is_skipped(self, channel):
        order = make_order(channel)
        Order.objects.filter(pk=order.pk).update(billing_email=None, shipping_email="")
        row = _row(order)

        _run(row.pk)

        row.refresh_from_db()
        assert row.status == OrderMailStatus.SKIPPED and mail.outbox == []

    def test_shop_without_an_email_channel_is_skipped(self, channel, email_channel):
        email_channel.delete()
        row = _row(make_order(channel))

        _run(row.pk)

        row.refresh_from_db()
        assert row.status == OrderMailStatus.SKIPPED

    def test_success_clears_the_error_of_an_earlier_attempt(self, channel):
        row = _row(make_order(channel))
        OrderMail.objects.filter(pk=row.pk).update(last_error="SMTPServerDisconnected: connection lost")

        _run(row.pk)

        row.refresh_from_db()
        assert (row.status, row.last_error) == (OrderMailStatus.SENT, "")

    def test_confirmation_of_an_order_canceled_meanwhile_is_skipped(self, channel):
        row = _row(make_order(channel, status=OrderStatus.CANCELED))

        _run(row.pk)

        row.refresh_from_db()
        assert row.status == OrderMailStatus.SKIPPED and mail.outbox == []

    def test_a_database_error_still_records_the_failure(self, channel, monkeypatch):
        from django.db import DatabaseError

        def broken_query(*args):
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM no_such_table")
            raise DatabaseError("unreachable")

        monkeypatch.setattr("django_checkout.services.order_mail_data.build_confirmation_data", broken_query)
        row = _row(make_order(channel))

        _run(row.pk)

        row.refresh_from_db()
        assert row.status == OrderMailStatus.FAILED and row.last_error.startswith("ProgrammingError")

    def test_broken_template_fails_at_once_without_retry(self, channel, retries, monkeypatch):
        monkeypatch.setattr("django_email.service.checkout.order_email.OrderEmailService.render", _broken)
        row = _row(make_order(channel))

        _run(row.pk)

        row.refresh_from_db()
        assert row.status == OrderMailStatus.FAILED and retries == []
        assert row.last_error.startswith("TemplateSyntaxError")


def _broken(self, data):
    from django.template import TemplateSyntaxError

    raise TemplateSyntaxError("unclosed tag")


@pytest.mark.django_db
class TestRetries:
    def test_smtp_error_schedules_a_retry_and_counts_the_attempt(self, channel, retries, monkeypatch):
        _smtp_down(monkeypatch)
        row = _row(make_order(channel))

        with pytest.raises(Retried):
            _run(row.pk)
        with pytest.raises(Retried):
            _run(row.pk, retries=1)

        row.refresh_from_db()
        assert (row.status, row.attempts) == (OrderMailStatus.PENDING, 2)
        assert row.last_error == "SMTPServerDisconnected: connection lost"
        assert retries == list(RETRY_COUNTDOWNS[:2])

    def test_last_failure_marks_failed_and_notifies_staff_once(self, channel, retries, monkeypatch):
        _smtp_down(monkeypatch)
        notified = _fake_notifications(monkeypatch)
        row = _row(make_order(channel))

        _run(row.pk, retries=len(RETRY_COUNTDOWNS))

        row.refresh_from_db()
        assert row.status == OrderMailStatus.FAILED and retries == []
        assert len(notified) == 1
        assert notified[0]["title"] == "Order e-mail not sent"
        assert notified[0]["subject_ref"] == f"checkout.Order:{row.order.order_id}"


@pytest.mark.django_db
def test_a_refused_recipient_is_masked_in_the_error(channel, retries, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise SMTPRecipientsRefused({BUYER: (550, b"mailbox unavailable")})

    monkeypatch.setattr("django_email.domain.EmailDomain.send_email", refuse)
    row = _row(make_order(channel))

    with pytest.raises(Retried):
        _run(row.pk)

    row.refresh_from_db()
    assert BUYER not in row.last_error and "<recipient>" in row.last_error


def _fake_notifications(monkeypatch) -> list[dict]:
    calls = []
    module = types.ModuleType("django_django_notifications_fake")
    module.notify = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "django_notifications.services.notify_service", module)
    real = order_mail_tasks.apps.is_installed
    monkeypatch.setattr(
        order_mail_tasks.apps, "is_installed", lambda label: label == "django_notifications" or real(label)
    )
    return calls


@pytest.mark.django_db(transaction=True)
def test_two_tasks_for_one_row_send_once(channel, email_channel, monkeypatch):
    """The first task holds the row lock while it sends; the second waits, then finds it sent."""
    row = _row(make_order(channel))
    sending, release = threading.Event(), threading.Event()
    sends = []
    real = order_mail_tasks._send

    def slow_send(mail_row, was_paid):
        sends.append(mail_row.pk)
        sending.set()
        release.wait(5)
        real(mail_row, was_paid)

    monkeypatch.setattr(order_mail_tasks, "_send", slow_send)
    first = threading.Thread(target=_in_thread, args=(row.pk,))
    second = threading.Thread(target=_in_thread, args=(row.pk,))
    first.start()
    sending.wait(5)
    second.start()
    second.join(0.5)
    release.set()
    first.join(5)
    second.join(5)

    assert sends == [row.pk]
    assert OrderMail.objects.get(pk=row.pk).status == OrderMailStatus.SENT


def _in_thread(mail_id: int) -> None:
    try:
        _run(mail_id)
    finally:
        connection.close()
