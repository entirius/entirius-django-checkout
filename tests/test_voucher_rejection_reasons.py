# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Why the voucher was refused must survive into the coverage error.

A cart paid for with a voucher alone and nothing else is blocked with
``voucher_insufficient_balance``. When the voucher was refused outright the plain
"covers 0 of 123" wording is a lie by omission: the customer is told to add funds when
the real problem is a typo in the code. The provider's rejection reasons now travel into
that error, both as prose and as machine-readable ``extra.voucher_errors``.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from django_utils.api.responses import ErrorInfo

from django_checkout.enums import PaymentProvider
from django_checkout.services import cart_service

SKU = "ENT-COFFEE-1KG"
GROSS = Decimal("123.00")
NET = Decimal("100.00")
TAX_RATE = Decimal("0.2300")

BUYER_ADDRESS = {
    "email": "buyer@novatrade.test",
    "firstname": "Jan",
    "lastname": "Kowalski",
    "country_code": "PL",
    "city": "Warsaw",
    "postcode": "00-001",
    "street": "Main 1",
    "dialling_code": "+48",
    "telephone": "123456789",
}


class _RejectingVoucherProvider:
    """Stand-in for the VoucherPaymentProvider, which ships outside this repository.

    Only the ``attach_to_cart`` contract documented on BasePaymentProvider matters here:
    it returns the errors that stopped the voucher from being applied.
    """

    rejections = [
        ErrorInfo(code="voucher_not_found", message="Voucher ENT-GIFT-404 does not exist."),
        ErrorInfo(code="voucher_expired", message="Voucher ENT-GIFT-OLD expired on 2026-01-31."),
    ]

    def __init__(self, payment_method, *args, **kwargs):
        self.payment_method = payment_method

    def attach_to_cart(self, cart, payment_data=None):
        return list(self.rejections)

    def clear_cart_attachments(self, cart):
        return None


@pytest.fixture
def priced_product(db, channel, default_country, default_currency):
    """One SKU that costs 123.00 EUR gross and has a unit on the shelf."""
    from django_pim.models import Channel as PimChannel
    from django_pim.models import FeatureSet, Product, RealProduct
    from django_pricemanager.models import Channel as PriceChannel
    from django_pricemanager.models import Price, PriceList, SaleChannel, TaxClass, TaxRate
    from django_pricemanager.models import ProductRepresentation as PricedProduct
    from django_pricemanager.models.pricelist import PriceListStatusEnum

    from django_checkout.models import ProductRepresentation, Stock, Supplier

    pim_channel = PimChannel.objects.create(
        idx=channel.idx,
        name="Novatrade",
        default_language=channel.default_language,
        default_currency=default_currency,
    )
    Product.objects.create(
        real_product=RealProduct.objects.create(sku=SKU),
        shop=pim_channel,
        feature_set=FeatureSet.objects.create(idx="default", name="Default"),
    )

    price_channel = PriceChannel.objects.create(idx=channel.idx, name="Novatrade")
    sale_channel = SaleChannel.objects.create(
        idx=f"{channel.idx}-pl", name="Novatrade PL", channel=price_channel, country=default_country
    )
    pricelist = PriceList.objects.create(
        sale_channel=sale_channel,
        currency=default_currency,
        country=default_country,
        status=PriceListStatusEnum.READY,
    )
    tax_class = TaxClass.objects.create(idx="standard", name="Standard")
    tax_rate = TaxRate.objects.create(tax_class=tax_class, country=default_country, rate=TAX_RATE)
    Price.objects.create(
        pricelist=pricelist,
        product=PricedProduct.objects.create(tax_class=tax_class, sku=SKU),
        net_value=NET,
        gross_value=GROSS,
        tax_rate=tax_rate,
    )

    supplier = Supplier.objects.create(code="kestrel", name="Kestrel Supply", is_global=True, channel=channel)
    Stock.objects.create(
        product=ProductRepresentation.objects.create(sku=SKU, channel=channel), supplier=supplier, quantity=10
    )
    return SKU


@pytest.fixture
def voucher_method(channel):
    from django_checkout.models import PaymentMethod

    method = PaymentMethod.objects.create(
        channel=channel, code="voucher", provider=PaymentProvider.VOUCHER, name_t9n={"en": "Gift card"}
    )
    method.countries.add(channel.default_country)
    method.currencies.add(channel.default_currency)
    return method


@pytest.fixture
def cart_awaiting_payment(channel, priced_product):
    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    cart_service.patch_addresses(
        channel=channel, cart_id=str(record.cart_id), billing=BUYER_ADDRESS, shipping=BUYER_ADDRESS
    )
    record, _ = cart_service.patch_items(
        channel=channel, cart_id=str(record.cart_id), items=[{"sku": SKU, "quantity": 1}]
    )
    return record


def _pay_with_voucher(channel, cart, provider):
    with patch("django_checkout.models.payment_method.VoucherPaymentProvider", provider):
        _record, messages = cart_service.patch_payment(
            channel=channel, cart_id=str(cart.cart_id), code="voucher", pay_code="ENT-GIFT-404"
        )
    return messages


def _coverage_error(messages):
    return next(m for m in messages if isinstance(m, ErrorInfo) and m.code == "voucher_insufficient_balance")


class TestRejectedVoucher:
    def test_the_reasons_replace_the_add_funds_wording(self, channel, cart_awaiting_payment, voucher_method):
        messages = _pay_with_voucher(channel, cart_awaiting_payment, _RejectingVoucherProvider)

        error = _coverage_error(messages)

        assert error.message == (
            "No voucher could be applied: Voucher ENT-GIFT-404 does not exist.; "
            "Voucher ENT-GIFT-OLD expired on 2026-01-31."
        )

    def test_the_reasons_are_machine_readable(self, channel, cart_awaiting_payment, voucher_method):
        messages = _pay_with_voucher(channel, cart_awaiting_payment, _RejectingVoucherProvider)

        error = _coverage_error(messages)

        assert error.extra["voucher_errors"] == [
            {"code": "voucher_not_found", "message": "Voucher ENT-GIFT-404 does not exist."},
            {"code": "voucher_expired", "message": "Voucher ENT-GIFT-OLD expired on 2026-01-31."},
        ]

    def test_the_amounts_survive_next_to_the_reasons(self, channel, cart_awaiting_payment, voucher_method):
        """The storefront still needs the numbers to render the payment prompt."""
        messages = _pay_with_voucher(channel, cart_awaiting_payment, _RejectingVoucherProvider)

        error = _coverage_error(messages)

        assert error.extra["voucher_amount_applied"] == "0"
        assert error.extra["cart_total"] == "123.00"
        assert error.extra["remaining_to_pay"] == "123.00"
        assert error.extra["currency_code"] == "EUR"


class TestNothingToReport:
    def test_a_silent_provider_keeps_the_add_funds_wording(self, channel, cart_awaiting_payment, voucher_method):
        """No rejection to report, so the original add-funds prompt is what the customer needs."""

        class _SilentProvider(_RejectingVoucherProvider):
            rejections = []

        messages = _pay_with_voucher(channel, cart_awaiting_payment, _SilentProvider)

        error = _coverage_error(messages)

        assert error.message.startswith("Voucher covers 0 EUR of 123.00 EUR.")
        assert "voucher_errors" not in error.extra

    def test_a_bare_string_rejection_is_not_promoted_to_a_reason(self, channel, cart_awaiting_payment, voucher_method):
        """Legacy providers return plain strings; those carry no code a client can act on."""

        class _StringProvider(_RejectingVoucherProvider):
            rejections = ["Voucher ENT-GIFT-404 does not exist."]

        messages = _pay_with_voucher(channel, cart_awaiting_payment, _StringProvider)

        error = _coverage_error(messages)

        assert error.message.startswith("Voucher covers 0 EUR of 123.00 EUR.")
        assert "voucher_errors" not in error.extra
