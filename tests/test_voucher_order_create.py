# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""``Order.create`` with a voucher next to another payment method.

The methods ran in the order the cart listed them. With the gateway listed first its session existed before the
voucher was debited: a debit that failed rolled the order back, and the gateway session stayed payable for an order
that does not exist. Cash on delivery and free order were read off entry ``[0]``, the voucher's, so a voucher with
cash on delivery left the order awaiting a payment that comes at the door.
"""

from decimal import Decimal

import pytest
from django.test import RequestFactory

from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Cart, Order, PaymentIntent, PaymentMethod, ProductRepresentation, Stock, Supplier
from tests.test_voucher_gateway_payloads import checkout, gateway_entry, payment_entry, voucher_entry


class VoucherDebitFailed(Exception):
    pass


class _Recording(BasePaymentProvider):
    calls: list = []
    fail = False

    def process_order(self, order, cart=None):
        self.calls.append(self.payment_method.code)
        if self.fail and self.payment_method.provider == PaymentProvider.VOUCHER:
            raise VoucherDebitFailed("balance changed")
        return ""


@pytest.fixture
def calls(monkeypatch):
    """Vouchers and PayU record when they are asked; the voucher debit fails when ``_Recording.fail`` is set."""
    recorded = []
    monkeypatch.setattr(_Recording, "calls", recorded)
    monkeypatch.setattr(_Recording, "fail", False)
    real = PaymentMethod.get_provider

    def get_provider(method):
        if method.provider in (PaymentProvider.VOUCHER, PaymentProvider.PAYU):
            return _Recording(method)
        return real(method)

    monkeypatch.setattr(PaymentMethod, "get_provider", get_provider)
    return recorded


@pytest.fixture
def shelf(channel):
    supplier = Supplier.objects.create(code="main", name="Main warehouse", is_global=True, channel=channel)
    Stock.objects.create(
        product=ProductRepresentation.objects.create(sku="ENT-LAMP-01", channel=channel), supplier=supplier, quantity=5
    )


@pytest.fixture
def methods(channel):
    for code, provider in (("voucher", PaymentProvider.VOUCHER), ("payu", PaymentProvider.PAYU)):
        PaymentMethod.objects.create(channel=channel, code=code, provider=provider)
    PaymentMethod.objects.create(channel=channel, code="cod", provider=PaymentProvider.COD)
    PaymentMethod.objects.create(channel=channel, code="free", provider=PaymentProvider.FREE_ORDER)


def _place(channel, payment_method):
    data = checkout(payment_method)
    cart = Cart.create(data, channel)
    cart.save()
    return Order.create(data, cart, channel, RequestFactory().post("/orders/"))


@pytest.mark.django_db
@pytest.mark.usefixtures("shelf", "methods")
class TestVoucherNextToAGateway:
    def test_the_voucher_is_debited_before_the_gateway_is_asked(self, channel, calls):
        _place(channel, [gateway_entry("payu"), voucher_entry()])

        assert calls == ["voucher", "payu"]

    def test_a_failed_debit_leaves_no_gateway_session(self, channel, calls, monkeypatch):
        monkeypatch.setattr(_Recording, "fail", True)

        with pytest.raises(VoucherDebitFailed):
            _place(channel, [gateway_entry("payu"), voucher_entry()])

        assert calls == ["voucher"]
        assert not Order.objects.exists()

    def test_listed_voucher_first_the_order_stays(self, channel, calls):
        _place(channel, [voucher_entry(), gateway_entry("payu")])

        assert calls == ["voucher", "payu"]


@pytest.mark.django_db
@pytest.mark.usefixtures("shelf", "methods")
class TestTheSettlingMethodDecides:
    def test_cash_on_delivery_next_to_a_voucher_confirms_the_order(self, channel, calls):
        order, _redirect, _error = _place(channel, [voucher_entry(), payment_entry("cod")])

        assert order.order_status == OrderStatus.CONFIRMED
        cod = PaymentIntent.objects.get(order=order, code="cod")
        assert cod.payment_status == PaymentIntentStatus.COMPLETE
        assert cod.amount == Decimal("93.00")

    def test_a_gateway_next_to_a_voucher_leaves_the_order_unpaid(self, channel, calls):
        order, _redirect, _error = _place(channel, [voucher_entry(), gateway_entry("payu")])

        assert order.order_status == OrderStatus.UNPAID

    def test_free_order_next_to_a_voucher_is_refused_while_something_is_due(self, channel, calls):
        """93.00 is left after the voucher: free order would confirm it unpaid."""
        with pytest.raises(Exception, match="free order with 93.00 to pay"):
            _place(channel, [voucher_entry(), payment_entry("free")])

        assert not Order.objects.exists()
