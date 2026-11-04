# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""``subtotal_*`` of a v2 cart is the items alone.

``CheckoutData.base_total`` adds shipping before its reduction and the payment fee, and ``total_tax`` adds their tax,
so a subtotal taken from them moved when the customer chose a shipping method: 100.00 items with free shipping
answered ``subtotal_gross`` 109.99 next to ``total_gross`` 100.00.
"""

from decimal import Decimal

import pytest

from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.domain.dto.item import ValidatedItemData
from django_checkout.enums import ItemStatus
from django_checkout.schemas.responses.cart import CartV2Response


def item(sku, status=ItemStatus.VALID):
    """100.00 gross at catalogue price, 23 % VAT: 81.30 net."""
    validated = ValidatedItemData.invalid_factory(sku=sku, status=status, is_gratis=False, quantity=1)
    validated.base_unit_price = validated.base_total_price = Decimal("100.00")
    validated.unit_price = validated.total_price = Decimal("100.00")
    validated.tax_rate = Decimal("23")
    return validated


def response(base_total, total, total_tax, fee_price=Decimal(0), items_tax=Decimal("18.70")):
    data = CheckoutData.factory()
    data.cart.items = [item("SKU-1"), item("SKU-GONE", status=ItemStatus.OUT_OF_STOCK)]
    data.cart.base_total_price = Decimal("100.00")
    data.cart.tax_amount = items_tax
    data.base_total = base_total
    data.total = total
    data.total_tax = total_tax
    data.fee_price = fee_price
    return CartV2Response.from_domain(data, cart_id="cart", cart_status="new")


@pytest.mark.parametrize(
    ("base_total", "total", "total_tax", "fee_price"),
    [
        (Decimal("100.00"), Decimal("100.00"), Decimal("18.70"), Decimal(0)),
        (Decimal("109.99"), Decimal("100.00"), Decimal("18.70"), Decimal(0)),
        (Decimal("112.30"), Decimal("112.30"), Decimal("21.00"), Decimal(0)),
        (Decimal("117.30"), Decimal("117.30"), Decimal("21.94"), Decimal("5.00")),
    ],
    ids=["no_shipping", "free_shipping", "paid_shipping", "shipping_and_fee"],
)
def test_subtotal_holds_only_the_items(base_total, total, total_tax, fee_price):
    cart = response(base_total, total, total_tax, fee_price)

    assert cart.subtotal_gross == "100.00"
    assert cart.subtotal_net == "81.30"
    assert cart.total_gross == str(total)


@pytest.mark.parametrize(
    "items_tax",
    [Decimal("16.83"), Decimal("14.96")],
    ids=["code_discount_10_percent", "special_price_80"],
)
def test_the_subtotal_net_is_the_catalogue_net_of_the_same_items(items_tax):
    """`cart.tax_amount` is the tax after discounts and special prices; the gross minus it is neither net."""
    cart = response(Decimal("100.00"), Decimal("90.00"), items_tax, items_tax=items_tax)

    assert cart.subtotal_gross == "100.00"
    assert cart.subtotal_net == "81.30"


def test_a_cart_without_tax_answers_the_gross_as_its_net():
    """0 % VAT, or a voucher that pays it all (``cart.tax_amount`` 0): the items' net is still there."""
    data = CheckoutData.factory()
    data.cart.items = [item("SKU-1")]
    data.cart.items[0].tax_rate = None
    data.cart.base_total_price = Decimal("100.00")
    data.cart.tax_amount = Decimal(0)

    cart = CartV2Response.from_domain(data, cart_id="cart", cart_status="new")

    assert cart.subtotal_net == cart.subtotal_gross == "100.00"


def test_a_cart_not_validated_yet_answers_a_zero_subtotal():
    data = CheckoutData.factory()

    cart = CartV2Response.from_domain(data, cart_id="cart", cart_status="new")

    assert cart.subtotal_gross == "0.00"
    assert cart.subtotal_net is None
