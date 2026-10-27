# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""An automatic rule keeps applying once the cart has stored it.

A write stores the automatic discount in ``cart.discounts``; a rule without codes is stored under its
name. The next write feeds the stored discounts back into ``validate_discounts``, which narrowed the
rules to the codes in the cart and so dropped the automatic rule: the discount was on after one write
and off after the next.
"""

from decimal import Decimal

import pytest

from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.dto.discount import DiscountData, ValidatedDiscountData
from django_checkout.domain.validators.discounts import validate_discounts
from django_checkout.enums import ItemStatus
from django_checkout.models import DiscountCode, DiscountRuleCode
from django_checkout.models.discount_rule_code import ModifiersForDiscountRule

TOTAL = Decimal("1098.00")


@pytest.fixture
def automatic_rule(channel):
    rule = DiscountRuleCode.objects.create(
        name="AUTO_5",
        modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT,
        extra_value=5,
        min_order_amount=Decimal("1000.00"),
        automatic_applications=True,
        combine_with_other_rules=True,
        priority=5,
    )
    rule.channels.add(channel)
    return rule


@pytest.fixture
def code_rule(channel):
    rule = DiscountRuleCode.objects.create(
        name="CODE_50",
        modifier=ModifiersForDiscountRule.PRICE_DISCOUNT,
        extra_value=50,
        min_order_amount=Decimal("0"),
        combine_with_other_rules=True,
        priority=10,
    )
    rule.channels.add(channel)
    DiscountCode.objects.create(rule=rule, code="COMBINE50", max_used=100)
    return rule


def stored_automatic(rule):
    """The discount entry a previous write left in the cart for an automatic rule."""
    return ValidatedDiscountData(
        code=rule.name,
        sku=None,
        quantity=None,
        clear_discounts=False,
        item_code=None,
        price_discount=0,
        percent_discount=5,
        free_shipping=False,
        free_shipping_methods=[],
        status=ItemStatus.VALID,
        free_order=False,
        min_order_amount=1000.0,
        extra_value=5,
        target="all",
        modifier=rule.modifier,
        is_automatic=True,
    )


def cart_with(*discounts):
    cart = CartData.factory()
    cart.discounts = list(discounts)
    return cart


def applied(validated):
    return {(rule.pk, discount.is_automatic) for discount, rule in validated if rule is not None}


@pytest.mark.django_db
def test_automatic_rule_applies_to_a_cart_without_discounts(channel, automatic_rule):
    assert applied(validate_discounts(cart_with(), TOTAL, channel)) == {(automatic_rule.pk, True)}


@pytest.mark.django_db
def test_automatic_rule_survives_its_stored_entry(channel, automatic_rule):
    cart = cart_with(stored_automatic(automatic_rule))
    assert applied(validate_discounts(cart, TOTAL, channel)) == {(automatic_rule.pk, True)}


@pytest.mark.django_db
def test_automatic_rule_combines_with_a_code(channel, automatic_rule, code_rule):
    cart = cart_with(
        DiscountData(code="COMBINE50", sku=None, quantity=None, clear_discounts=False),
        stored_automatic(automatic_rule),
    )
    assert applied(validate_discounts(cart, TOTAL, channel)) == {(code_rule.pk, False), (automatic_rule.pk, True)}


@pytest.mark.django_db
def test_automatic_rule_below_min_order_amount_stays_off(channel, automatic_rule):
    cart = cart_with(stored_automatic(automatic_rule))
    assert applied(validate_discounts(cart, Decimal("999.99"), channel)) == set()
