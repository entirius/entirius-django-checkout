# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What each gateway is asked for when a voucher pays part of the order.

An order body holds a list of payment methods: ``[voucher, gateway]`` when a voucher covers part of the cart. A
gateway read entry ``[0]`` of that list, which is the voucher's: no return URL (Stripe's ``success_url``, PayU's
``continueUrl``), no card token, and a BLIK code that was the voucher's code. The total the gateways charge is
already net of the voucher, while PayPal's breakdown listed the parts without it, so it did not add up to the
amount and PayPal refused the order.

Nothing here calls a gateway: the tests read the request each provider builds.
"""

from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from django.test import RequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.domain.dto.address import Address, AddressData, BillingAddress
from django_checkout.domain.dto.cart import CartData, CheckoutData
from django_checkout.domain.dto.item import ValidatedItemData
from django_checkout.domain.dto.payment import ValidatedPaymentData
from django_checkout.domain.dto.shipping import ValidatedShippingData
from django_checkout.enums import ItemStatus, PaymentProvider, ValidationStatus
from django_checkout.models import Order, PaymentMethod
from django_checkout.utils import sanitize

CONTINUE_URL = "https://shop.example/thanks/<order_id>"
VOUCHER_CODE = "26WT****"
BLIK_CODE = "777123"
CARD_TOKEN = "SINGLE-card-token-1"  # noqa: S105 - synthetic, a single-use token has no TOK_ prefix

# 2 x 61.50 = 123.00 gross (100.00 net + 23.00 tax), shipping 10.00: 133.00 before the voucher.
# A 40.00 voucher leaves 93.00 for the gateway.
VOUCHER = Decimal("40.00")
LEFT_TO_PAY = Decimal("93.00")

GATEWAYS = {
    "payu": (PaymentProvider.PAYU, {"client_id": "1", "client_secret": "s", "pos_id": "1"}),
    "payu-card": (PaymentProvider.PAYU_CARD, {"client_id": "1", "client_secret": "s", "pos_id": "1"}),
    "blik": (PaymentProvider.PAYU_BLIK, {"client_id": "1", "client_secret": "s", "pos_id": "1"}),
    "paypal": (
        PaymentProvider.PAYPAL,
        {
            "client_id": "1",
            "client_secret": "s",
            "return_url": "https://shop.example/paypal/return",
            "cancel_url": "https://shop.example/paypal/cancel",
        },
    ),
    "stripe": (PaymentProvider.STRIPE, {"api_key": "sk_test_synthetic", "automatic_tax": True}),
    "p24": (
        PaymentProvider.PRZELEWY24,
        {"client_id": "1000", "regulation_accept": True, "crc_key": "c", "return_url": "https://shop.example/p24"},
    ),
    "paynow": (PaymentProvider.PAYNOW, {"api_key": "k", "signature_key": "s"}),
    "autopay": (PaymentProvider.AUTOPAY, {"service_id": {"test-channel": "100"}}),
}


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


def payment_entry(code, **values):
    entry = ValidatedPaymentData(
        code=code,
        name=code,
        card=None,
        bank_id=None,
        country_code="PL",
        authorization_token=None,
        continue_url=None,
        save_card=False,
        status=ItemStatus.VALID,
    )
    return replace(entry, **values)


def voucher_entry():
    return payment_entry("voucher", pay_code=VOUCHER_CODE)


def gateway_entry(code):
    return payment_entry(code, continue_url=CONTINUE_URL, card=CARD_TOKEN, pay_code=BLIK_CODE)


def _lamps():
    """2 x 61.50, as the cart stores a validated line."""
    line = ValidatedItemData.invalid_factory(sku="ENT-LAMP-01", status=ItemStatus.VALID, is_gratis=False, quantity=2)
    line.name = "Desk lamp"
    line.unit_price, line.total_price = Decimal("61.50"), Decimal("123.00")
    line.base_unit_price, line.base_total_price = Decimal("61.50"), Decimal("123.00")
    line.unit_price_netto, line.total_price_netto = Decimal("50.00"), Decimal("100.00")
    line.base_unit_tax_amount, line.base_total_tax_amount = Decimal("11.50"), Decimal("23.00")
    line.tax_rate = Decimal("0.23")
    return line


def _courier():
    return ValidatedShippingData(
        code="courier",
        name="Courier",
        country_code="PL",
        delivery_point=None,
        base_unit_price=Decimal("10.00"),
        base_total_price=Decimal("10.00"),
        discount_amount=Decimal("0.00"),
        unit_price=Decimal("10.00"),
        total_price=Decimal("10.00"),
        cash_on_delivery_fee=None,
        free_delivery_above=None,
        free_delivery_above_modifier=None,
        status=ItemStatus.VALID,
    )


def checkout(payment_method, voucher=VOUCHER):
    """A valid cart: 2 lamps and a courier, ``voucher`` already taken off the total as checkout nets it."""
    cart = CartData.factory()
    cart.items = [_lamps()]
    cart.discount_amount = Decimal("0.00")
    cart.base_total_price = Decimal("123.00")
    cart.base_netto_price = Decimal("100.00")
    cart.total_price = Decimal("123.00") - voucher
    cart.validation_status = ValidationStatus.VALID
    cart.voucher_amount_applied = voucher or None
    return CheckoutData(
        cart=cart,
        addresses=AddressData(
            shipping_address=_address(Address),
            billing_address=_address(BillingAddress),
            validation_status=ValidationStatus.VALID,
        ),
        payment_method=payment_method,
        shipping_method=_courier(),
        total_to_min_order_price=None,
        base_total=Decimal("133.00"),
        total=cart.total_price + Decimal("10.00"),
        fee_price=Decimal("0.00"),
        fee_tax_price=Decimal("0.00"),
        fee_tax_rate=Decimal("0.00"),
        total_tax=Decimal("23.00"),
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
def gateway(channel):
    """Make the voucher and one gateway method on the channel, return the gateway's provider."""

    def make(code):
        provider, additional_data = GATEWAYS[code]
        PaymentMethod.objects.create(channel=channel, code="voucher", provider=PaymentProvider.VOUCHER)
        method = PaymentMethod.objects.create(
            channel=channel, code=code, provider=provider, additional_data=additional_data
        )
        gateway_provider = method.get_provider()
        gateway_provider.request = RequestFactory().post("/orders/", REMOTE_ADDR="203.0.113.7")
        gateway_provider.get_notify_url = lambda *args: "https://shop.example/notify"
        return gateway_provider

    return make


def _order(channel, payment_method, voucher=VOUCHER):
    return Order.objects.create(channel=channel, order_body=sanitize(asdict(checkout(payment_method, voucher))))


def _voucher_first(channel, code):
    return _order(channel, [voucher_entry(), gateway_entry(code)])


def _thanks(order):
    return CONTINUE_URL.replace("<order_id>", order.pretty_id)


@pytest.mark.django_db
class TestTheGatewayReadsItsOwnEntry:
    @pytest.mark.parametrize("code", ["payu", "payu-card", "blik"])
    def test_payu_returns_the_buyer_to_the_gateway_entry_url(self, channel, gateway, code):
        provider = gateway(code)
        order = _voucher_first(channel, code)

        assert provider.build_payu_data(order).continueUrl == _thanks(order)

    def test_payu_card_charges_the_card_of_its_own_entry(self, channel, gateway):
        provider = gateway("payu-card")
        order = _voucher_first(channel, "payu-card")

        pay_methods = provider.build_payu_data(order).payMethods

        assert (pay_methods.payMethod.type, pay_methods.payMethod.value) == ("CARD_TOKEN", CARD_TOKEN)

    def test_blik_sends_the_blik_code_never_the_voucher_code(self, channel, gateway):
        provider = gateway("blik")
        order = _voucher_first(channel, "blik")

        pay_methods = provider.build_payu_data(order).payMethods

        assert (pay_methods.payMethod.type, pay_methods.payMethod.value) == ("BLIK_AUTHORIZATION_CODE", BLIK_CODE)

    def test_blik_alone_sends_its_code(self, channel, gateway):
        """The list shape hid the code from BLIK on every order, with or without a voucher."""
        provider = gateway("blik")
        order = _order(channel, [gateway_entry("blik")], voucher=Decimal("0"))

        pay_methods = provider.build_payu_data(order).payMethods

        assert pay_methods is not None
        assert pay_methods.payMethod.value == BLIK_CODE

    def test_stripe_success_url_is_the_gateway_entry_url(self, channel, gateway):
        provider = gateway("stripe")
        order = _voucher_first(channel, "stripe")

        assert provider.map_order(order)["success_url"] == _thanks(order)


@pytest.mark.django_db
class TestTheGatewayChargesWhatTheVoucherLeft:
    @pytest.mark.parametrize("code", ["payu", "payu-card", "blik"])
    def test_payu(self, channel, gateway, code):
        provider = gateway(code)

        assert provider.build_payu_data(_voucher_first(channel, code)).totalAmount == "9300"

    def test_przelewy24(self, channel, gateway):
        provider = gateway("p24")
        order = _voucher_first(channel, "p24")

        transaction = provider.map_przelewy24_transaction(None, order, [], None)

        assert transaction.amount == 9300

    def test_paynow(self, channel, gateway):
        provider = gateway("paynow")

        assert provider.build_paynow_data(_voucher_first(channel, "paynow")).amount == 9300

    def test_autopay(self, channel, gateway, monkeypatch):
        monkeypatch.setattr(checkout_settings, "AUTOPAY_HASH_KEY", {"test-channel": "synthetic-hash-key"})
        provider = gateway("autopay")

        assert provider.map_order(_voucher_first(channel, "autopay"))["Amount"] == str(LEFT_TO_PAY)

    def test_paypal_breakdown_adds_up_to_the_amount(self, channel, gateway):
        """PayPal refuses an order whose breakdown does not sum to its amount; the voucher rides in ``discount``."""
        provider = gateway("paypal")
        order = _voucher_first(channel, "paypal")

        amount = provider.build_paypal_data(order).purchase_units[0].amount
        parts = amount.breakdown
        added_up = (
            Decimal(parts.item_total.value)
            + Decimal(parts.tax_total.value)
            + Decimal(parts.shipping.value)
            + Decimal(parts.handling.value)
            - Decimal(parts.shipping_discount.value)
            - Decimal(parts.discount.value)
        )

        assert Decimal(amount.value) == LEFT_TO_PAY
        assert added_up == LEFT_TO_PAY
        assert Decimal(parts.discount.value) == VOUCHER

    def test_paypal_without_a_voucher_is_unchanged(self, channel, gateway):
        provider = gateway("paypal")
        order = _order(channel, [gateway_entry("paypal")], voucher=Decimal("0"))

        amount = provider.build_paypal_data(order).purchase_units[0].amount

        assert Decimal(amount.value) == Decimal("133.00")
        assert Decimal(amount.breakdown.discount.value) == Decimal("0")
