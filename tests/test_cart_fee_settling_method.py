# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The cart charges the fee and the cash on delivery surcharge of the method that settles what the vouchers leave.

``process_cart`` read both off entry ``[0]`` of ``payment_method``. With a voucher listed first, ``[voucher, cod]``,
that is the voucher: the shipping lost its cash on delivery surcharge and the cart its payment fee, while
``Order.create`` already treats the order as cash on delivery. Listed the other way round, ``[payu, voucher]``, a
gateway's flat fee stayed on a cart the vouchers cover in full, so the customer was sent to the gateway to pay the
fee alone.

The coffee costs 123.00 EUR, the courier 10.00 with a 5.00 cash on delivery surcharge, cash on delivery and PayU
carry a flat 2.00 fee. The voucher module ships outside this repository: the tests answer its signal with the
amount the voucher covers.
"""

import json
from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from django_checkout.enums import FeeType, PaymentProvider
from django_checkout.models import Cart, PaymentMethod, ShippingMethod, ShippingOption
from django_checkout.services import cart_service
from django_checkout.signals import compute_cart_voucher_total_signal
from django_checkout.views.cart import get_or_put_cart
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    TRIGGER_SKU,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)
from tests.test_voucher_rejection_reasons import _RejectingVoucherProvider

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
FLAT_FEE = 2.0


class _AcceptingVoucherProvider(_RejectingVoucherProvider):
    rejections = []


def _method(channel, code, provider, fee=0.0):
    method = PaymentMethod.objects.create(
        channel=channel,
        code=code,
        provider=provider,
        name_t9n={"en": code},
        fee_type=FeeType.FLAT_FEE if fee else FeeType.WITHOUT_FEE,
        fee_value=fee,
    )
    method.countries.add(channel.default_country)
    method.currencies.add(channel.default_currency)
    return method


def _shipping(channel, code, price, cod_fee=None):
    method = ShippingMethod.objects.create(channel=channel, code=code, name_t9n={"en": code})
    # ShippingOption.save() calls cache.delete_pattern() (django-redis); the test cache lacks it.
    with patch("django_checkout.models.shipping_option.cache"):
        ShippingOption.objects.create(
            method=method,
            currency=channel.default_currency,
            country_code="ALL",
            price_brutto=Decimal(price),
            tax_rate=Decimal("0.23"),
            cash_on_delivery_available=cod_fee is not None,
            cash_on_delivery_fee=Decimal(cod_fee) if cod_fee is not None else None,
        )


@pytest.fixture
def shop(channel, priced_catalog, coffee_in_stock):  # noqa: F811
    _shipping(channel, "courier", "10.00", cod_fee="5.00")
    _shipping(channel, "pickup", "0.00")
    _method(channel, "voucher", PaymentProvider.VOUCHER)
    _method(channel, "cod", PaymentProvider.COD, fee=FLAT_FEE)
    _method(channel, "payu", PaymentProvider.PAYU, fee=FLAT_FEE)
    return channel


@pytest.fixture
def voucher_covers(monkeypatch):
    """The voucher module's answer: the voucher covers ``amount`` of the coffee line."""
    covered = {"amount": Decimal("0")}

    def receiver(sender, cart_id, cart_items=None, **kwargs):
        line = sum((gross for sku, gross in cart_items or [] if sku == TRIGGER_SKU), Decimal("0"))
        amount = min(covered["amount"], line)
        return {"total": amount, "per_sku": {TRIGGER_SKU: amount}, "applied_count": 1}

    monkeypatch.setattr("django_checkout.settings.USE_VALIDATE_VOUCHERS_SIGNAL", True)
    monkeypatch.setattr("django_checkout.models.payment_method.VoucherPaymentProvider", _AcceptingVoucherProvider)
    compute_cart_voucher_total_signal.connect(receiver, dispatch_uid="test-fee-voucher")
    yield covered
    compute_cart_voucher_total_signal.disconnect(dispatch_uid="test-fee-voucher")


def _cart(channel, shipping):
    record, _ = cart_service.create_cart(
        channel=channel,
        items=[{"sku": TRIGGER_SKU, "quantity": 1}],
        currency_code="EUR",
        language_code="en",
        country_code="PL",
    )
    cart_service.patch_addresses(channel, str(record.cart_id), BUYER, BUYER)
    record, _ = cart_service.patch_shipping(channel, str(record.cart_id), shipping)
    return record


def _pay(api_key, channel, record, *codes):
    """v1 PATCH with the payment list, as a storefront that pays with a voucher next to another method sends it."""
    entries = [{"code": code, "pay_code": "ENTGIFT1"} if code == "voucher" else {"code": code} for code in codes]
    return _patch_payment(api_key, channel, record, entries)


def _patch_payment(api_key, channel, record, entries):
    request = APIRequestFactory().patch(
        "/", json.dumps({"payment_method": entries}), content_type="application/json", HTTP_X_API_KEY=api_key.key
    )
    response = get_or_put_cart(request, channel_idx=channel.idx, cart_id=str(record.cart_id), version="1")
    assert response.status_code == 200, response.content
    return Cart.objects.get(pk=record.pk).as_data


@pytest.mark.django_db
class TestCashOnDeliveryNextToAVoucher:
    @pytest.mark.parametrize("codes", [("voucher", "cod"), ("cod", "voucher")])
    def test_the_surcharge_and_the_fee_are_charged_in_either_order(self, api_key, shop, voucher_covers, codes):
        voucher_covers["amount"] = Decimal("23.00")
        data = _pay(api_key, shop, _cart(shop, "courier"), *codes)

        assert data.shipping_method.total_price == Decimal("15.00")  # courier 10.00 + cash on delivery 5.00
        assert data.fee_price == Decimal("2.00")
        assert data.total == Decimal("117.00")  # 123.00 - 23.00 voucher + 15.00 shipping + 2.00 fee
        assert data.validation_status == "valid"


@pytest.mark.django_db
class TestAGatewayNextToAVoucher:
    @pytest.mark.parametrize("codes", [("voucher", "payu"), ("payu", "voucher")])
    def test_the_gateway_fee_is_charged_on_what_it_settles(self, api_key, shop, voucher_covers, codes):
        voucher_covers["amount"] = Decimal("23.00")
        data = _pay(api_key, shop, _cart(shop, "courier"), *codes)

        assert data.fee_price == Decimal("2.00")
        assert data.total == Decimal("112.00")  # 123.00 - 23.00 voucher + 10.00 shipping + 2.00 fee

    @pytest.mark.parametrize("codes", [("voucher", "payu"), ("payu", "voucher")])
    def test_a_cart_the_voucher_covers_in_full_pays_no_fee(self, api_key, shop, voucher_covers, codes):
        """Nothing is left for PayU: the cart drops it, and its fee must not bring it back for 2.00."""
        voucher_covers["amount"] = Decimal("123.00")
        data = _pay(api_key, shop, _cart(shop, "pickup"), *codes)

        assert data.fee_price == Decimal("0")
        assert data.total == Decimal("0")
        assert [entry.code for entry in data.payment_method] == ["voucher"]


@pytest.mark.django_db
def test_a_single_method_keeps_its_fee(api_key, shop, voucher_covers):
    """No voucher: the method is entry [0] and the settling method alike."""
    data = _pay(api_key, shop, _cart(shop, "courier"), "cod")

    assert data.shipping_method.total_price == Decimal("15.00")
    assert data.fee_price == Decimal("2.00")
    assert data.total == Decimal("140.00")
