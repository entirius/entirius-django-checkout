# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A price discount adds to what earlier rules already took off an item.

Rules are applied one after another. The price discount gives its last item the rest of its amount
(so the shares add up despite rounding) and assigned that rest instead of adding it: a 5 % rule
followed by a 50 off rule left 50 off the item instead of 5 % + 50.
"""

from decimal import Decimal
from types import SimpleNamespace

from django_checkout.models import DiscountRuleCode
from django_checkout.models.discount_rule_code import ModifiersForDiscountRule
from django_checkout.worker.discount_worker import calculate_discounts_modifiers


class Cart:
    def __init__(self, *items, rules=1):
        self.items = list(items)
        self.discounts = [SimpleNamespace(item_code=None, price_discount=0, percent_discount=0) for _ in range(rules)]
        self.total_price = sum(item.total_price for item in items)
        self.total_netto_price = self.total_price

    def get_valid_items(self):
        return self.items


def item(sku, total_price):
    total_price = Decimal(total_price)
    return SimpleNamespace(
        sku=sku,
        quantity=1,
        total_price=total_price,
        total_price_netto=total_price,
        discount_amount=Decimal(0),
        discount_amount_netto=Decimal(0),
        tax_rate=None,
        total_tax_amount=None,
        is_gratis=False,
    )


def rule(modifier, extra_value):
    return DiscountRuleCode(modifier=modifier, extra_value=extra_value)


PERCENT_5 = rule(ModifiersForDiscountRule.PERCENT_DISCOUNT, 5)
PRICE_50 = rule(ModifiersForDiscountRule.PRICE_DISCOUNT, 50)


def apply(cart, *rules):
    skus = [SimpleNamespace(sku=i.sku) for i in cart.items]
    for idx, r in enumerate(rules):
        cart = calculate_discounts_modifiers(r, cart, skus, discount_idx=idx, currency_code="EUR")
    return cart


def test_price_discount_alone_splits_its_amount():
    cart = apply(Cart(item("A", "300.00"), item("B", "100.00")), PRICE_50)
    assert [i.discount_amount for i in cart.items] == [Decimal("37.50"), Decimal("12.50")]
    assert cart.discount_amount == Decimal("50.00")


def test_price_discount_after_a_percent_discount_on_one_item():
    cart = apply(Cart(item("A", "1318.92"), rules=2), PERCENT_5, PRICE_50)
    assert cart.items[0].discount_amount == Decimal("115.95")
    assert cart.total_price == Decimal("1202.97")


def test_price_discount_after_a_percent_discount_on_several_items():
    cart = apply(Cart(item("A", "300.00"), item("B", "100.00"), rules=2), PERCENT_5, PRICE_50)
    assert [i.discount_amount for i in cart.items] == [Decimal("52.50"), Decimal("17.50")]
    assert cart.discount_amount == Decimal("70.00")
