# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Which orders get a confirmation and a cancellation e-mail, and when: one per purchase, one per canceled order.

The hooks only register after-commit callbacks; ``on_commit()`` runs them, and the ones they register in turn.
"""

from decimal import Decimal

import pytest
from django.test import RequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.domain.payment_provider.notification_state import apply_payment_status
from django_checkout.enums import OrderMailKind, OrderMailStatus, OrderStatus, PaymentIntentStatus
from django_checkout.models import Cart, Order, OrderMail, PaymentIntent
from django_checkout.services import order_mail
from tests.order_mail_support import enqueued, mails, make_order, method, on_commit, set_status, split_purchase
from tests.test_voucher_gateway_payloads import checkout, gateway_entry, payment_entry, voucher_entry
from tests.test_voucher_order_create import calls, methods, shelf

__all__ = ["calls", "enqueued", "methods", "on_commit", "shelf"]  # fixtures

CANCELED = OrderMailKind.CANCELED


def _place(channel, entries, voucher=Decimal("0")):
    """``voucher`` covers the items; then the shipping is taken off too, nothing is left to pay."""
    data = checkout(entries, voucher)
    if voucher:
        data.total = Decimal("0.00")
    cart = Cart.create(data, channel)
    cart.save()
    return Order.create(data, cart, channel, RequestFactory().post("/orders/"))[0]


def _gateway_intent(order):
    return PaymentIntent.objects.create(order=order, method=method(order.channel, "payu"), code="payu")


def _notify(order, payment_status=PaymentIntentStatus.COMPLETE):
    apply_payment_status(PaymentIntent.objects.get(order=order), payment_status)


@pytest.fixture
def transfer(channel):
    method(channel, "transfer")


# --- placement (Order.create) ---


@pytest.mark.django_db
@pytest.mark.usefixtures("shelf", "methods", "transfer", "calls")
class TestPlacement:
    @pytest.mark.parametrize("entry", ["transfer", "cod"])
    def test_offline_payment_gets_one_confirmation_at_placement(self, channel, enqueued, on_commit, entry):
        with on_commit():
            order = _place(channel, [payment_entry(entry)])

        assert [row.status for row in mails(order)] == [OrderMailStatus.PENDING]
        assert enqueued == [(mails(order)[0].pk, None)]

    def test_free_order_gets_one(self, channel, enqueued, on_commit):
        with on_commit():
            order = _place(channel, [payment_entry("free")], voucher=Decimal("123.00"))

        assert order.order_status == OrderStatus.CONFIRMED
        assert len(mails(order)) == 1 and len(enqueued) == 1

    def test_voucher_covered_order_gets_one_though_both_hooks_fire(self, channel, enqueued, on_commit):
        with on_commit():
            order = _place(channel, [voucher_entry(), gateway_entry("payu")], voucher=Decimal("123.00"))

        assert order.order_status == OrderStatus.CONFIRMED
        assert len(mails(order)) == 1 and len(enqueued) == 1

    def test_gateway_order_awaiting_payment_gets_none(self, channel, enqueued, on_commit):
        with on_commit():
            order = _place(channel, [gateway_entry("payu")])

        assert order.order_status == OrderStatus.UNPAID
        assert mails(order) == [] and enqueued == []


# --- transitions (Order.save) ---


@pytest.mark.django_db
class TestConfirmedTransition:
    def test_gateway_order_confirmed_by_notification_gets_one(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")
        _gateway_intent(order)

        with on_commit():
            _notify(order)
        with on_commit():
            _notify(order)  # the same notification delivered again

        assert len(mails(order)) == 1 and len(enqueued) == 1

    def test_gateway_order_canceled_then_paid_gets_one(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")
        _gateway_intent(order)
        with on_commit():
            set_status(order, OrderStatus.CANCELED)
        assert mails(order) == [] and mails(order, CANCELED) == []

        with on_commit():
            _notify(order)

        assert len(mails(order)) == 1

    def test_admin_confirming_a_gateway_order_sends_one(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")

        with on_commit():
            set_status(order, OrderStatus.CONFIRMED)

        assert len(mails(order)) == 1

    def test_order_canceled_before_payment_gets_nothing(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")
        _gateway_intent(order)

        with on_commit():
            _notify(order, PaymentIntentStatus.CANCELLED)

        assert OrderMail.objects.count() == 0 and enqueued == []


# --- split purchases ---


@pytest.mark.django_db
class TestSplitPurchase:
    def test_transfer_purchase_gets_one_confirmation_on_the_lead(self, channel, enqueued, on_commit):
        lead, second = make_order(channel), make_order(channel)
        split_purchase(channel, [lead, second])

        with on_commit():
            order_mail.schedule_confirmation_after_commit(second.pk)
            order_mail.schedule_confirmation_after_commit(lead.pk)

        assert len(mails(lead)) == 1 and mails(second) == []
        assert len(enqueued) == 1

    def test_gateway_purchase_waits_for_the_lead_to_be_confirmed(self, channel, enqueued, on_commit):
        lead, second = make_order(channel, "payu"), make_order(channel, "payu")
        split_purchase(channel, [lead, second])
        with on_commit():
            order_mail.schedule_confirmation_after_commit(lead.pk)
        assert OrderMail.objects.count() == 0

        with on_commit():
            set_status(lead, OrderStatus.CONFIRMED)  # confirms the second part too

        assert len(mails(lead)) == 1 and mails(second) == []

    def test_order_create_skips_a_part_the_split_worker_schedules(self, channel, enqueued, on_commit):
        lead = make_order(channel, "cod", status=OrderStatus.CONFIRMED)
        split_purchase(channel, [lead])
        cart = Cart.objects.get(split_cart__split_order=lead)

        from django_checkout.models.order import _schedule_confirmation_unless_split_part

        with on_commit():
            _schedule_confirmation_unless_split_part(lead, cart)

        assert OrderMail.objects.count() == 0


# --- cancellation ---


@pytest.mark.django_db
class TestCancellation:
    def test_confirmed_transfer_order_canceled_gets_one(self, channel, enqueued, on_commit):
        order = make_order(channel, "transfer", status=OrderStatus.CONFIRMED)
        with on_commit():
            order_mail.schedule_confirmation_after_commit(order.pk)

        with on_commit():
            set_status(order, OrderStatus.CANCELED)
        with on_commit():
            order_mail.schedule_canceled_after_commit(order.pk, True)  # canceled again: no-op

        assert len(mails(order, CANCELED)) == 1
        assert enqueued[-1] == (mails(order, CANCELED)[0].pk, True)

    def test_unpaid_gateway_order_canceled_gets_nothing(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")

        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        assert mails(order, CANCELED) == []

    def test_paid_gateway_order_canceled_is_told_it_was_paid(self, channel, enqueued, on_commit):
        order = make_order(channel, "payu")
        with on_commit():
            set_status(order, OrderStatus.CONFIRMED)

        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        assert enqueued[-1] == (mails(order, CANCELED)[0].pk, True)

    def test_cash_on_delivery_order_canceled_is_not_told_it_was_paid(self, channel, enqueued, on_commit):
        order = make_order(channel, "cod", status=OrderStatus.CONFIRMED)
        with on_commit():
            order_mail.schedule_confirmation_after_commit(order.pk)

        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        assert enqueued[-1] == (mails(order, CANCELED)[0].pk, False)

    def test_free_order_canceled_is_not_told_it_was_paid(self, channel, enqueued, on_commit):
        order = make_order(channel, "free", status=OrderStatus.CONFIRMED)
        with on_commit():
            order_mail.schedule_confirmation_after_commit(order.pk)

        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        assert enqueued[-1] == (mails(order, CANCELED)[0].pk, False)

    def test_one_part_of_a_split_purchase_canceled_gets_its_own(self, channel, enqueued, on_commit):
        lead, second = make_order(channel), make_order(channel)
        split_purchase(channel, [lead, second])
        with on_commit():
            order_mail.schedule_confirmation_after_commit(lead.pk)

        with on_commit():
            set_status(second, OrderStatus.CANCELED)

        assert len(mails(second, CANCELED)) == 1 and mails(lead, CANCELED) == []

    def test_setting_off_sends_none(self, channel, enqueued, on_commit, monkeypatch):
        monkeypatch.setattr(checkout_settings, "CHECKOUT_ORDER_CANCELED_EMAIL", False)
        order = make_order(channel, "transfer", status=OrderStatus.CONFIRMED)
        with on_commit():
            order_mail.schedule_confirmation_after_commit(order.pk)

        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        assert mails(order, CANCELED) == []


# --- switched off, not installed, failing ---


@pytest.mark.django_db
class TestGuards:
    def test_confirmation_setting_off_creates_no_row(self, channel, enqueued, on_commit, monkeypatch):
        monkeypatch.setattr(checkout_settings, "CHECKOUT_ORDER_CONFIRMATION_EMAIL", False)
        order = make_order(channel, "payu")

        with on_commit():
            set_status(order, OrderStatus.CONFIRMED)

        assert OrderMail.objects.count() == 0

    def test_without_django_email_no_row_and_no_error(self, channel, enqueued, on_commit, monkeypatch):
        real = order_mail.apps.is_installed
        monkeypatch.setattr(order_mail.apps, "is_installed", lambda label: label != "django_email" and real(label))
        order = make_order(channel, "payu")

        with on_commit():
            set_status(order, OrderStatus.CONFIRMED)

        assert OrderMail.objects.count() == 0

    def test_a_failing_service_never_fails_order_save(self, channel, enqueued, on_commit, monkeypatch):
        monkeypatch.setattr(order_mail, "schedule_confirmation", _boom)
        monkeypatch.setattr(order_mail, "schedule_canceled", _boom)
        order = make_order(channel, "payu")

        with on_commit():
            set_status(order, OrderStatus.CONFIRMED)
        with on_commit():
            set_status(order, OrderStatus.CANCELED)

        order.refresh_from_db()
        assert order.order_status == OrderStatus.CANCELED

    @pytest.mark.usefixtures("shelf", "methods", "transfer", "calls")
    def test_a_failing_service_never_fails_order_create(self, channel, enqueued, on_commit, monkeypatch):
        monkeypatch.setattr(order_mail, "schedule_confirmation", _boom)

        with on_commit():
            order = _place(channel, [payment_entry("transfer")])

        assert Order.objects.filter(pk=order.pk).exists()


@pytest.mark.django_db
def test_a_broker_that_refuses_the_task_leaves_the_row_failed(channel, on_commit, monkeypatch):
    from django_checkout.worker import order_mail_tasks

    monkeypatch.setattr(order_mail_tasks.send_order_mail, "delay", _boom)
    order = make_order(channel, "transfer")

    with on_commit():
        order_mail.schedule_confirmation_after_commit(order.pk)

    row = mails(order)[0]
    assert (row.status, row.last_error) == (OrderMailStatus.FAILED, "RuntimeError: not enqueued")


def _boom(*args):
    raise RuntimeError("service down")
