# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""``gratis.rules`` of a v2 cart comes from ``available_gratis_rules``.

The cart keeps the rules there. ``gratis_rules`` is a legacy field nothing fills, so ``rules`` stayed empty next to
``is_available: true``.
"""

from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.schemas.responses.cart import CartV2Response

RULE = {"code": "GRATIS_STEP", "max_available_quantity": 1, "items": [{"sku": "GIFT-1", "price": 2.78}]}


def test_the_rules_come_from_the_field_the_cart_fills():
    data = CheckoutData.factory()
    data.cart.is_gratis_available = True
    data.cart.available_gratis_rules = [RULE]

    gratis = CartV2Response.from_domain(data, cart_id="cart", cart_status="new").gratis

    assert gratis.is_available is True
    assert gratis.rules == [RULE]


def test_a_cart_without_gift_rules_answers_an_empty_list():
    gratis = CartV2Response.from_domain(CheckoutData.factory(), cart_id="cart", cart_status="new").gratis

    assert gratis.is_available is False
    assert gratis.rules == []
