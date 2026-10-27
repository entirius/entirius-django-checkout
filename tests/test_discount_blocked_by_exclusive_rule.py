# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A valid code that loses priority arbitration is reported as blocked, not as unknown.

The lowest priority wins: a combinable winner keeps only combinable rules, an exclusive winner keeps
only itself. A code dropped that way exists and is valid, so ``code_not_found`` sent the customer
looking for a typo; it is ``blocked_by_exclusive_rule`` and names the rule that blocked it.
"""

from decimal import Decimal

import pytest

from django_checkout.api.v2.exceptions import map_discount_errors
from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.dto.discount import DiscountData, DiscountsRejectedError
from django_checkout.domain.validators.discounts import validate_discounts
from django_checkout.enums import ItemStatus
from django_checkout.models import DiscountCode, DiscountRuleCode
from django_checkout.models.discount_rule_code import ModifiersForDiscountRule

TOTAL = Decimal("1098.00")


def make_rule(channel, name, *, priority, combinable, code=None, automatic=False):
    rule = DiscountRuleCode.objects.create(
        name=name,
        modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT,
        extra_value=5,
        min_order_amount=Decimal("0"),
        automatic_applications=automatic,
        combine_with_other_rules=combinable,
        priority=priority,
    )
    rule.channels.add(channel)
    if code:
        DiscountCode.objects.create(rule=rule, code=code, max_used=100)
    return rule


def cart_with(*codes):
    cart = CartData.factory()
    cart.discounts = [DiscountData(code=code, sku=None, quantity=None, clear_discounts=False) for code in codes]
    return cart


def rejections(validated):
    return {
        discount.code: (discount.rejection_reason, discount.rejection_meta)
        for discount, _ in validated
        if discount.status == ItemStatus.INVALID
    }


@pytest.mark.django_db
def test_exclusive_code_blocked_by_higher_priority_automatic_rule(channel):
    make_rule(channel, "AUTO_5", priority=5, combinable=True, automatic=True)
    make_rule(channel, "EXCLUSIVE", priority=10, combinable=False, code="EXCL10")

    validated = validate_discounts(cart_with("EXCL10"), TOTAL, channel)

    assert rejections(validated) == {
        "EXCL10": (
            "blocked_by_exclusive_rule",
            {"code": "EXCL10", "blocking_code": "AUTO_5", "blocking_priority": 5},
        )
    }


@pytest.mark.django_db
def test_combinable_code_blocked_by_higher_priority_exclusive_code(channel):
    make_rule(channel, "EXCLUSIVE", priority=1, combinable=False, code="EXCL1")
    make_rule(channel, "COMBINE", priority=10, combinable=True, code="COMB10")

    validated = validate_discounts(cart_with("EXCL1", "COMB10"), TOTAL, channel)

    assert rejections(validated) == {
        "COMB10": (
            "blocked_by_exclusive_rule",
            {"code": "COMB10", "blocking_code": "EXCL1", "blocking_priority": 1},
        )
    }


@pytest.mark.django_db
def test_unknown_code_next_to_arbitration_stays_not_found(channel):
    make_rule(channel, "EXCLUSIVE", priority=1, combinable=False, code="EXCL1")

    validated = validate_discounts(cart_with("EXCL1", "NO_SUCH_CODE"), TOTAL, channel)

    assert rejections(validated) == {"NO_SUCH_CODE": ("code_not_found", {"code": "NO_SUCH_CODE"})}


@pytest.mark.django_db
def test_blocked_code_maps_to_v2_error_without_the_priority(channel):
    make_rule(channel, "AUTO_5", priority=5, combinable=True, automatic=True)
    make_rule(channel, "EXCLUSIVE", priority=10, combinable=False, code="EXCL10")
    validated = [discount for discount, _ in validate_discounts(cart_with("EXCL10"), TOTAL, channel)]
    error = DiscountsRejectedError(
        message="Some discounts are invalid",
        code="one_or_more_discount_invalid",
        rejected=[d for d in validated if d.status == ItemStatus.INVALID],
        sent_codes=["EXCL10"],
        applied_codes=[d.code for d in validated if d.status == ItemStatus.VALID],
    )

    assert [(e["field"], e["code"], e["meta"]) for e in map_discount_errors(error)] == [
        ("codes[0].code", "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE", {"code": "EXCL10", "blocking_code": "AUTO_5"})
    ]
