# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What a Stripe Checkout session charges for an order with a cart discount.

A line's ``discount_amount`` covers the whole line, and its ``unit_amount`` was the unit price less that discount.
Stripe multiplies ``unit_amount`` by the quantity, so the discount came off once per unit: 3 x 54.99 at 15 % off
charged 90.72 instead of 140.22, and at 50 % off the unit amount went below zero and Stripe refused the session.
At quantity 1 both figures agree, which is why it went unnoticed.

Stripe is never called: ``Session.create`` and ``Coupon.create`` are replaced and their arguments read back.
"""

from dataclasses import asdict
from decimal import Decimal

import pytest
import stripe
from django.test import RequestFactory

from django_checkout.domain.dto.address import Address, AddressData, BillingAddress
from django_checkout.domain.dto.cart import CartData, CheckoutData
from django_checkout.domain.dto.item import ValidatedItemData
from django_checkout.domain.dto.payment import ValidatedPaymentData
from django_checkout.domain.dto.shipping import ValidatedShippingData
from django_checkout.enums import ItemStatus, PaymentProvider, ValidationStatus
from django_checkout.models import Order, PaymentMethod
from django_checkout.utils import sanitize

SHIPPING = Decimal("10.00")


def _line(sku, unit_price, quantity, discount="0.00", total=None, is_gratis=False):
    line = ValidatedItemData.invalid_factory(sku=sku, status=ItemStatus.VALID, is_gratis=is_gratis, quantity=quantity)
    line.name = sku
    line.unit_price = line.base_unit_price = Decimal(unit_price)
    line.total_price = Decimal(total) if total is not None else Decimal(unit_price) * quantity
    line.base_total_price = Decimal(unit_price) * quantity
    line.discount_amount = Decimal(discount)
    return line


def _address(cls):
    return cls(
        email="buyer@shop.example",
        firstname="Ada",
        lastname="Nowak",
        country_code="PL",
        city="Gdansk",
        postcode="80-001",
        street="Long 1",
        dialling_code="+48",
        telephone="500600700",
        company=None,
    )


def _courier():
    return ValidatedShippingData(
        code="courier",
        name="Courier",
        country_code="PL",
        delivery_point=None,
        base_unit_price=SHIPPING,
        base_total_price=SHIPPING,
        discount_amount=Decimal("0.00"),
        unit_price=SHIPPING,
        total_price=SHIPPING,
        cash_on_delivery_fee=None,
        free_delivery_above=None,
        free_delivery_above_modifier=None,
        status=ItemStatus.VALID,
    )


def _checkout(lines, items_total):
    """A cart whose ``total_price`` is ``items_total``: the items net of the cart discount (and a voucher)."""
    cart = CartData.factory()
    cart.items = lines
    cart.discount_amount = sum((line.discount_amount for line in lines), Decimal("0"))
    cart.base_total_price = cart.base_netto_price = sum((line.base_total_price for line in lines), Decimal("0"))
    cart.total_price = Decimal(items_total)
    cart.validation_status = ValidationStatus.VALID
    stripe_entry = ValidatedPaymentData(
        code="stripe",
        name="Stripe",
        card=None,
        bank_id=None,
        country_code="PL",
        authorization_token=None,
        continue_url="https://shop.example/thanks/<order_id>",
        save_card=False,
        status=ItemStatus.VALID,
    )
    return CheckoutData(
        cart=cart,
        addresses=AddressData(
            shipping_address=_address(Address),
            billing_address=_address(BillingAddress),
            validation_status=ValidationStatus.VALID,
        ),
        payment_method=[stripe_entry],
        shipping_method=_courier(),
        total_to_min_order_price=None,
        base_total=cart.base_total_price + SHIPPING,
        total=cart.total_price + SHIPPING,
        fee_price=Decimal("0.00"),
        fee_tax_price=Decimal("0.00"),
        fee_tax_rate=Decimal("0.00"),
        total_tax=None,
        language_code="pl",
        currency_code="PLN",
        country_code="PL",
        requested_delivery_date=None,
        need_full_address=False,
        custom_order_id=None,
        split_order=False,
        split_by_feature_idx=None,
        split_by_attr_idx=None,
        original_order_id=None,
        comment=None,
        affiliate_code=None,
        customer_ip=None,
        validation_status=ValidationStatus.VALID,
    )


@pytest.fixture
def stripe_calls(monkeypatch):
    """Record what would go to Stripe; the session and the coupon come back with fixed ids."""
    calls = {"session": None, "coupons": []}

    def create_session(**kwargs):
        calls["session"] = kwargs
        return {"id": "cs_test_1", "url": "https://checkout.stripe.example/cs_test_1"}

    def create_coupon(**kwargs):
        calls["coupons"].append(kwargs)
        return stripe.Coupon.construct_from({"id": "co_test_1"}, "sk_test_synthetic")

    monkeypatch.setattr(stripe.checkout.Session, "create", create_session)
    monkeypatch.setattr(stripe.Coupon, "create", create_coupon)
    return calls


@pytest.fixture
def pay(channel):
    """Create the order and run the Stripe provider on it, as Order.create does."""
    method = PaymentMethod.objects.create(
        channel=channel,
        code="stripe",
        provider=PaymentProvider.STRIPE,
        additional_data={"api_key": "sk_test_synthetic", "automatic_tax": True},
    )

    def pay(lines, items_total):
        data = _checkout(lines, items_total)
        order = Order.objects.create(channel=channel, order_body=sanitize(asdict(data)))
        provider = method.get_provider()
        provider.request = RequestFactory().post("/orders/")
        provider.process_order(order)
        assert not provider.error
        return data

    return pay


def _charged(calls):
    """What the session charges, in grosze: every line times its quantity, less the coupon."""
    lines = sum(line["price_data"]["unit_amount"] * line["quantity"] for line in calls["session"]["line_items"])
    return lines - sum(coupon["amount_off"] for coupon in calls["coupons"])


def _to_grosze(amount):
    return int(amount * 100)


@pytest.mark.django_db
class TestADiscountOnALineOfSeveralUnits:
    def test_15_percent_off_three_units_charges_the_order_total(self, pay, stripe_calls):
        data = pay([_line("ENT-MUG-1", "54.99", 3, discount="24.75")], items_total="140.22")

        assert _charged(stripe_calls) == _to_grosze(data.total) == 15022

    def test_the_line_carries_the_unit_price_and_the_coupon_the_discount(self, pay, stripe_calls):
        pay([_line("ENT-MUG-1", "54.99", 3, discount="24.75")], items_total="140.22")

        line = stripe_calls["session"]["line_items"][0]
        assert (line["price_data"]["unit_amount"], line["quantity"]) == (5499, 3)
        assert [coupon["amount_off"] for coupon in stripe_calls["coupons"]] == [2475]
        assert stripe_calls["session"]["discounts"] == [{"coupon": "co_test_1"}]

    def test_half_off_no_longer_sends_a_negative_unit_amount(self, pay, stripe_calls):
        data = pay([_line("ENT-MUG-1", "54.99", 3, discount="82.48")], items_total="82.49")

        assert all(line["price_data"]["unit_amount"] >= 0 for line in stripe_calls["session"]["line_items"])
        assert _charged(stripe_calls) == _to_grosze(data.total)

    def test_one_unit_charges_what_it_charged_before(self, pay, stripe_calls):
        data = pay([_line("ENT-LAMP-1", "61.50", 1, discount="6.15")], items_total="55.35")

        assert _charged(stripe_calls) == _to_grosze(data.total) == 6535

    def test_several_lines_with_their_own_discounts(self, pay, stripe_calls):
        lines = [_line("ENT-MUG-1", "54.99", 3, discount="24.75"), _line("ENT-LAMP-1", "61.50", 2, discount="12.30")]

        data = pay(lines, items_total="250.92")

        assert _charged(stripe_calls) == _to_grosze(data.total)


@pytest.mark.django_db
class TestWhatElseTheCartTakesOff:
    def test_a_voucher_netted_from_the_total_is_not_charged_again(self, pay, stripe_calls):
        """The voucher module takes the voucher off ``cart.total_price``; the lines keep the sold price."""
        data = pay([_line("ENT-MUG-1", "54.99", 3, discount="24.75")], items_total="90.22")

        assert _charged(stripe_calls) == _to_grosze(data.total) == 10022

    def test_a_gift_is_charged_its_gift_price(self, pay, stripe_calls):
        """A gift line keeps its product's unit price and holds the gift price in ``total_price``."""
        lines = [
            _line("ENT-LAMP-1", "61.50", 1),
            _line("ENT-SHADE-1", "20.00", 1, total="0.01", is_gratis=True),
        ]

        data = pay(lines, items_total="61.51")

        assert _charged(stripe_calls) == _to_grosze(data.total) == 7151

    def test_no_reduction_no_coupon(self, pay, stripe_calls):
        data = pay([_line("ENT-MUG-1", "54.99", 3)], items_total="164.97")

        assert stripe_calls["coupons"] == []
        assert "discounts" not in stripe_calls["session"]
        assert _charged(stripe_calls) == _to_grosze(data.total)


@pytest.mark.django_db
class TestTheCoupon:
    def test_it_is_single_use_in_the_order_currency_on_the_method_account(self, pay, stripe_calls):
        pay([_line("ENT-MUG-1", "54.99", 3, discount="24.75")], items_total="140.22")

        coupon = stripe_calls["coupons"][0]
        assert (coupon["currency"], coupon["duration"], coupon["max_redemptions"]) == ("pln", "once", 1)
        assert coupon["api_key"] == "sk_test_synthetic"

    def test_its_idempotency_key_carries_the_amount(self, pay, stripe_calls):
        """A retry after the cart changed must not get back the coupon of the old amount."""
        pay([_line("ENT-MUG-1", "54.99", 3, discount="24.75")], items_total="140.22")

        assert stripe_calls["coupons"][0]["idempotency_key"].endswith("-2475")
