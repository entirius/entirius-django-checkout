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
from django_checkout.schemas.responses.cart import CartV2Response


def response(base_total, total, total_tax, fee_price=Decimal(0)):
    data = CheckoutData.factory()
    data.cart.base_total_price = Decimal("100.00")
    data.cart.tax_amount = Decimal("18.70")
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


def test_a_cart_not_validated_yet_answers_a_zero_subtotal():
    data = CheckoutData.factory()

    cart = CartV2Response.from_domain(data, cart_id="cart", cart_status="new")

    assert cart.subtotal_gross == "0.00"
    assert cart.subtotal_net is None
