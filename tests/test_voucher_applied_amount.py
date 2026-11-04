# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A voucher pays what it takes off the cart, and its balance is debited by that amount only.

``_apply_voucher_total`` clipped the cart's reduction to the total but stored the voucher module's figure as
``voucher_amount_applied``: a voucher larger than what the cart had left reported more than it took off, and the
voucher's payment intent and PayPal's ``discount`` read that. The voucher module was handed each line at its price
before discounts, so with a discount code it kept the larger amount on the cart and debited it at the order: the
customer lost the discount's worth of gift-card balance. The order now hands it what the charged lines cost; while
the cart is edited it still gets the lines at their price, since the module keeps any amount it lowers.

The coffee costs 123.00 EUR, a 10 % code takes 12.30 off it, the courier costs 10.00. The voucher module ships
outside this repository: ``FakeVoucherModule`` answers its signal the way it reconciles (an applied amount clamped
to the eligible lines it is handed, debited at the order) and holds a 200.00 balance.
"""

import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory

from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import PaymentProvider
from django_checkout.models import (
    Cart,
    DiscountModeOfAction,
    PaymentIntent,
    PaymentMethod,
    ShippingMethod,
    ShippingOption,
)
from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType
from django_checkout.services import cart_service, order_service
from django_checkout.signals import compute_cart_voucher_total_signal
from django_checkout.views.cart import get_or_put_cart
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _stock,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)
from tests.test_v2_order_code_usage import code_rule

BUYER = {
    "email": "buyer@example.org",
    "firstname": "Jan",
    "lastname": "Kowalski",
    "country_code": "PL",
    "city": "Warsaw",
    "postcode": "00-001",
    "street": "Main 1",
    "dialling_code": "+48",
    "telephone": "123456789",
}


class FakeVoucherModule:
    """One gift card on the cart: what ``reconcile_applied_vouchers`` and ``redeem_for_order`` do with it."""

    def __init__(self):
        self.balance = Decimal("200.00")
        self.applied = None  # CartVoucher.amount_applied: what the order will debit
        self.debited = Decimal("0")
        self.per_sku = True

    def attach(self):
        self.applied = self.balance  # applied before the cart is known: the whole balance, clamped below

    def reconcile(self, sender, cart_id, cart_items=None, **kwargs):
        """Clamp to the lines handed in and keep it, then spread it over them pro rata, last line the rest."""
        if self.applied is None:
            return None
        lines = [(sku, gross) for sku, gross in cart_items or [] if gross > 0]
        eligible = sum((gross for _sku, gross in lines), Decimal("0"))
        self.applied = min(self.applied, eligible)
        if not self.per_sku:
            return {"total": self.balance, "per_sku": {}, "applied_count": 1}
        per_sku, spread = {}, Decimal("0")
        for index, (sku, gross) in enumerate(lines):
            share = self.applied - spread if index == len(lines) - 1 else (gross / eligible * self.applied)
            per_sku[sku] = share.quantize(Decimal("0.01"))
            spread += per_sku[sku]
        return {"total": self.applied, "per_sku": per_sku, "applied_count": 1}

    def redeem(self):
        self.debited = self.applied
        self.balance -= self.applied


@pytest.fixture
def voucher(monkeypatch):
    module = FakeVoucherModule()

    class Provider(BasePaymentProvider):
        def attach_to_cart(self, cart, payment_data=None):
            # The stored pay_code is masked: only a request that sends the code attaches it again.
            entries = payment_data if isinstance(payment_data, list) else [payment_data]
            if any("*" not in (getattr(entry, "pay_code", None) or "*") for entry in entries):
                module.attach()
            return []

        def get_payment_method_extension(self, cart=None):
            return {"applied_count": 1 if module.applied else 0}

        def process_order(self, order, cart):
            module.redeem()
            return ""

        def get_processed_amount(self, order):
            return module.debited

    monkeypatch.setattr("django_checkout.settings.USE_VALIDATE_VOUCHERS_SIGNAL", True)
    monkeypatch.setattr("django_checkout.models.payment_method.VoucherPaymentProvider", Provider)
    compute_cart_voucher_total_signal.connect(module.reconcile, dispatch_uid="test-voucher-applied")
    yield module
    compute_cart_voucher_total_signal.disconnect(dispatch_uid="test-voucher-applied")


@pytest.fixture
def shop(channel, priced_catalog, coffee_in_stock):  # noqa: F811
    courier = ShippingMethod.objects.create(channel=channel, code="courier", name_t9n={"en": "Courier"})
    # ShippingOption.save() calls cache.delete_pattern() (django-redis); the test cache lacks it.
    with patch("django_checkout.models.shipping_option.cache"):
        ShippingOption.objects.create(
            method=courier,
            currency=channel.default_currency,
            country_code="ALL",
            price_brutto=Decimal("10.00"),
            tax_rate=Decimal("0.23"),
        )
    for code, provider in (("voucher", PaymentProvider.VOUCHER), ("transfer", PaymentProvider.TRANSFER)):
        method = PaymentMethod.objects.create(channel=channel, code=code, provider=provider, name_t9n={"en": code})
        method.countries.add(channel.default_country)
        method.currencies.add(channel.default_currency)
    return channel


def _cart(api_key, channel, codes=("WELCOME10",), items=({"sku": TRIGGER_SKU, "quantity": 1},)):
    """The items with the discount codes, the courier, then the voucher next to a bank transfer (v1 PATCH)."""
    record, _ = cart_service.create_cart(
        channel=channel,
        items=list(items),
        currency_code="EUR",
        language_code="en",
        country_code="PL",
    )
    cart_id = str(record.cart_id)
    cart_service.patch_addresses(channel, cart_id, BUYER, BUYER)
    cart_service.patch_shipping(channel, cart_id, "courier")
    cart_service.patch_discounts(channel, cart_id, [{"code": code} for code in codes])
    entries = [{"code": "voucher", "pay_code": "ENTGIFT1"}, {"code": "transfer"}]
    request = APIRequestFactory().patch(
        "/", json.dumps({"payment_method": entries}), content_type="application/json", HTTP_X_API_KEY=api_key.key
    )
    response = get_or_put_cart(request, channel_idx=channel.idx, cart_id=cart_id, version="1")
    assert response.status_code == 200, response.content
    return Cart.objects.get(cart_id=cart_id)


def _place(cart):
    """The order path: ``process_cart`` for the order, then ``Order.create``."""
    result = order_service.create_order(cart.channel, str(cart.cart_id), request=RequestFactory().post("/orders/"))
    return result.order


@pytest.mark.django_db
class TestAVoucherLargerThanTheCart:
    def test_it_takes_off_the_discounted_price(self, api_key, shop, voucher):
        code_rule(shop)

        data = _cart(api_key, shop).as_data

        assert data.cart.voucher_amount_applied == Decimal("110.70")  # 123.00 less the 10 % code
        assert data.total == Decimal("10.00")  # the courier, paid by transfer

    def test_its_balance_keeps_what_the_cart_did_not_take(self, api_key, shop, voucher):
        code_rule(shop)
        order = _place(_cart(api_key, shop))

        assert voucher.debited == Decimal("110.70")
        assert voucher.balance == Decimal("89.30")
        intents = dict(PaymentIntent.objects.filter(order=order).values_list("code", "amount"))
        assert intents == {"voucher": Decimal("110.70"), "transfer": Decimal("10.00")}

    def test_a_code_added_and_removed_leaves_the_voucher_cover_alone(self, api_key, shop, voucher):
        """The module keeps any amount it lowers: editing the cart must not hand it the discounted line."""
        code_rule(shop)
        cart = _cart(api_key, shop)

        cart, _messages = cart_service.patch_discounts(shop, str(cart.cart_id), [], clear=True)

        assert voucher.applied == Decimal("123.00")
        assert cart.as_data.cart.voucher_amount_applied == Decimal("123.00")
        assert cart.as_data.total == Decimal("10.00")

    def test_a_receiver_answering_a_total_only_is_held_to_the_cart(self, api_key, shop, voucher):
        """The signal may answer ``{"total": ...}`` without a split by product: the reduction was clipped, the
        reported amount was not."""
        voucher.per_sku = False

        data = _cart(api_key, shop, codes=()).as_data

        assert data.cart.voucher_amount_applied == Decimal("123.00")
        assert data.total == Decimal("10.00")


@pytest.fixture
def coffee_only_code(shop, catalog):  # noqa: F811
    """The 10 % code takes 12.30 off the coffee and nothing off the mug."""
    rule = code_rule(shop).rule
    mode_of_action = DiscountModeOfAction.objects.create(
        rule=rule, is_inclusion_or_exclusion=DiscountModeOfActionType.INCLUSION
    )
    mode_of_action.products.add(catalog[TRIGGER_SKU])


@pytest.mark.django_db
@pytest.mark.usefixtures("coffee_only_code")
class TestTwoLinesOneDiscounted:
    """Coffee 123.00 less 12.30, mug 24.60: the cart costs 135.30, a gift card of exactly that covers it."""

    @pytest.fixture(autouse=True)
    def mug(self, shop, supplier):  # noqa: F811
        _stock(shop, supplier, GIFT_SKU, quantity=10)

    def test_the_card_covers_the_cart_while_it_is_edited(self, api_key, shop, voucher):
        voucher.balance = Decimal("135.30")

        data = _cart(api_key, shop, items=({"sku": TRIGGER_SKU, "quantity": 1}, {"sku": GIFT_SKU, "quantity": 1}))
        data = data.as_data

        assert data.cart.voucher_amount_applied == Decimal("135.30")
        assert data.total == Decimal("10.00")

    def test_the_order_debits_the_whole_card(self, api_key, shop, voucher):
        voucher.balance = Decimal("135.30")

        order = _place(
            _cart(api_key, shop, items=({"sku": TRIGGER_SKU, "quantity": 1}, {"sku": GIFT_SKU, "quantity": 1}))
        )

        assert voucher.debited == Decimal("135.30")
        assert order.as_data.total == Decimal("10.00")


@pytest.mark.django_db
def test_a_voucher_smaller_than_the_cart_is_taken_whole(api_key, shop, voucher):
    voucher.balance = Decimal("50.00")
    code_rule(shop)

    order = _place(_cart(api_key, shop))

    assert order.as_data.cart.voucher_amount_applied == Decimal("50.00")
    assert order.as_data.total == Decimal("70.70")  # 110.70 - 50.00 voucher + 10.00 courier
    assert voucher.balance == Decimal("0.00")
