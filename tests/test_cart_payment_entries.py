# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Every entry of ``payment_method`` counts for the cart's status and for product limits, not only entry ``[0]``.

The cart was ``valid`` when its first entry was: ``[payu, voucher]`` with a voucher code the provider refused as
malformed stayed valid, while ``[voucher, payu]`` with the same code was invalid. Product limits set for a shipping
and payment method pair were checked against entry ``[0]`` only, so a product the shop does not send by courier
with cash on delivery went through as ``[voucher, cod]``.
"""

import pytest

from django_checkout.enums import ItemStatus
from django_checkout.models import LimitedProducts, PaymentMethod, ShippingMethod
from tests.test_cart_fee_settling_method import (  # noqa: F401 - fixtures
    _cart,
    _patch_payment,
    _pay,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    shop,
    supplier,
    voucher_covers,
)
from tests.test_gratis_stock import TRIGGER_SKU


@pytest.mark.django_db
@pytest.mark.usefixtures("voucher_covers")
class TestTheStatusReadsEveryEntry:
    @pytest.mark.parametrize("order", ["voucher first", "voucher last"])
    def test_a_refused_voucher_entry_makes_the_cart_invalid(self, api_key, shop, order):  # noqa: F811
        entries = [{"code": "payu"}, {"code": "voucher", "pay_code": "bad code!"}]
        if order == "voucher first":
            entries.reverse()

        data = _patch_payment(api_key, shop, _cart(shop, "courier"), entries)

        assert data.validation_status == "invalid"

    @pytest.mark.parametrize("codes", [("voucher", "payu"), ("payu", "voucher")])
    def test_valid_entries_keep_the_cart_valid(self, api_key, shop, codes):  # noqa: F811
        data = _pay(api_key, shop, _cart(shop, "courier"), *codes)

        assert data.validation_status == "valid"


@pytest.fixture
def no_courier_cash_on_delivery(shop):  # noqa: F811
    """The coffee is not sent by courier with cash on delivery."""
    LimitedProducts.objects.create(
        idx=TRIGGER_SKU,
        shipping_method=ShippingMethod.objects.get(channel=shop, code="courier"),
        payment_method=PaymentMethod.objects.get(channel=shop, code="cod"),
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("voucher_covers", "no_courier_cash_on_delivery")
class TestProductLimitsApplyToEveryMethod:
    @pytest.mark.parametrize("codes", [("voucher", "cod"), ("cod", "voucher"), ("cod",)])
    def test_the_limited_product_is_refused(self, api_key, shop, codes):  # noqa: F811
        data = _pay(api_key, shop, _cart(shop, "courier"), *codes)

        coffee = next(item for item in data.cart.items if item.sku == TRIGGER_SKU)
        assert coffee.status == ItemStatus.INVALID
        assert data.validation_status == "invalid"

    def test_another_method_is_not_limited(self, api_key, shop):  # noqa: F811
        data = _pay(api_key, shop, _cart(shop, "courier"), "voucher", "payu")

        coffee = next(item for item in data.cart.items if item.sku == TRIGGER_SKU)
        assert coffee.status == ItemStatus.VALID
