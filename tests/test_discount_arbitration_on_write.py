# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Priority arbitration runs against the validated cart items on every write.

A write that sends items (v1 cart PUT, v2 PATCH items) handed ``validate_discounts`` the request
items, which carry no status. Arbitration matches rules against VALID items only, so no rule matched,
no rule won, and an exclusive code applied together with every automatic rule.
"""

from decimal import Decimal

import pytest

from django_checkout.models import DiscountCode, DiscountRuleCode, ModifiersForDiscountRule
from django_checkout.services import cart_service
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    TRIGGER_SKU,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)


def _rule(channel, name, *, priority, combinable, automatic=False):
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
    return rule


@pytest.fixture
def exclusive_code_and_automatic_rule(channel):
    _rule(channel, "AUTO_5", priority=20, combinable=True, automatic=True)
    exclusive = _rule(channel, "EXCLUSIVE_10", priority=10, combinable=False)
    DiscountCode.objects.create(rule=exclusive, code="EXCL10", max_used=100)


def applied_codes(record):
    return sorted(d["code"] for d in record.cart_body["cart"]["discounts"] if d["status"] == "valid")


@pytest.mark.django_db
@pytest.mark.usefixtures("priced_catalog", "coffee_in_stock", "exclusive_code_and_automatic_rule")
def test_exclusive_code_keeps_automatic_rule_out_after_an_items_write(channel):
    record, _ = cart_service.create_cart(
        channel=channel, items=[{"sku": TRIGGER_SKU, "quantity": 1}], currency_code="EUR", country_code="PL"
    )
    record, _ = cart_service.patch_discounts(channel=channel, cart_id=str(record.cart_id), codes=[{"code": "EXCL10"}])
    assert applied_codes(record) == ["EXCL10"]

    record, _ = cart_service.patch_items(
        channel=channel, cart_id=str(record.cart_id), items=[{"sku": TRIGGER_SKU, "quantity": 2}]
    )

    assert applied_codes(record) == ["EXCL10"]
