# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 cart writes report why each rejected discount code was rejected.

The cart's ``_errors`` keeps ``ONE_OR_MORE_DISCOUNT_INVALID`` and adds one detail per rejected code: its v2 code,
``codes[i].code`` (its place among the codes sent) and public meta. v1 responses don't change.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.exceptions import map_discount_errors
from django_checkout.api.v2.views.cart_views import CartDiscountsView
from django_checkout.domain.dto.discount import DiscountsRejectedError, ValidatedDiscountData
from django_checkout.enums import ItemStatus
from django_checkout.models import DiscountCode, DiscountRuleCode, ModifiersForDiscountRule
from django_checkout.services import cart_service
from django_checkout.views.cart import prepare_errors_and_messages
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    TRIGGER_SKU,
    catalog,
    coffee_in_stock,
    gratis_code,
    gratis_rule,
    pim_channel,
    priced_catalog,
    supplier,
)

factory = APIRequestFactory()
SUMMARY = "ONE_OR_MORE_DISCOUNT_INVALID"


def make_rule(channel, name, *codes, priority=10, combinable=True, automatic=False, min_order="0"):
    rule = DiscountRuleCode.objects.create(
        name=name,
        modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT,
        extra_value=5,
        min_order_amount=Decimal(min_order),
        automatic_applications=automatic,
        combine_with_other_rules=combinable,
        priority=priority,
    )
    rule.channels.add(channel)
    for code in codes:
        DiscountCode.objects.create(rule=rule, code=code, max_used=100)
    return rule


@pytest.fixture
def coffee_cart(channel, priced_catalog, coffee_in_stock):  # noqa: F811
    """One kilo of coffee: 123.00 gross."""
    record, _ = cart_service.create_cart(
        channel=channel, items=[{"sku": TRIGGER_SKU, "quantity": 1}], currency_code="EUR", country_code="PL"
    )
    return record


@pytest.fixture
def send_codes(channel, coffee_cart, make_api_key):
    key = make_api_key(channel=channel)

    def send(*codes) -> list[dict]:
        request = factory.patch("/", {"codes": [{"code": c} for c in codes]}, format="json", HTTP_X_API_KEY=key)
        response = CartDiscountsView.as_view()(request, channel_idx=channel.idx, cart_id=str(coffee_cart.cart_id))
        assert response.status_code == 200
        return response.data.get("_errors", [])

    return send


def per_code(errors: list[dict]) -> list[tuple]:
    assert [e["code"] for e in errors if e["field"] == "cart.discounts.code"] == [SUMMARY]  # kept for old clients
    return [(e["field"], e["code"], e["meta"]) for e in errors if e["scope"] == "discounts" and e["code"] != SUMMARY]


@pytest.mark.django_db
def test_code_blocked_by_an_exclusive_code_names_it(channel, send_codes):
    make_rule(channel, "EXCLUSIVE", "EXCL1", priority=1, combinable=False)
    make_rule(channel, "COMBINE", "COMB10", priority=10)

    assert per_code(send_codes("EXCL1", "COMB10")) == [
        ("codes[1].code", "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE", {"code": "COMB10", "blocking_code": "EXCL1"})
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("entered", ["EXCL_A", "EXCL_B"])
def test_blocking_code_is_the_one_the_customer_entered(channel, send_codes, entered):
    """The winning rule has two codes; whichever the customer typed is the one named."""
    make_rule(channel, "EXCLUSIVE", "EXCL_A", "EXCL_B", priority=1, combinable=False)
    make_rule(channel, "COMBINE", "COMB10", priority=10)

    assert per_code(send_codes(entered, "COMB10")) == [
        ("codes[1].code", "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE", {"code": "COMB10", "blocking_code": entered})
    ]


@pytest.mark.django_db
def test_code_blocked_by_an_automatic_rule_names_it_as_the_cart_shows_it(channel, send_codes):
    make_rule(channel, "AUTO_5", priority=5, automatic=True)
    make_rule(channel, "EXCLUSIVE", "EXCL10", priority=10, combinable=False)

    assert per_code(send_codes("EXCL10")) == [
        ("codes[0].code", "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE", {"code": "EXCL10", "blocking_code": "AUTO_5"})
    ]


@pytest.mark.django_db
def test_second_code_of_the_same_rule_is_a_duplicate(channel, send_codes):
    make_rule(channel, "TEN", "TEN_A", "TEN_B")

    assert per_code(send_codes("TEN_A", "TEN_B")) == [("codes[1].code", "DISCOUNT_DUPLICATE_RULE", {"code": "TEN_B"})]


@pytest.mark.django_db
def test_same_code_sent_twice_rejects_the_second_copy(channel, send_codes):
    make_rule(channel, "TEN", "TEN_A")

    assert per_code(send_codes("TEN_A", "TEN_A")) == [("codes[1].code", "DISCOUNT_DUPLICATE_RULE", {"code": "TEN_A"})]


@pytest.mark.django_db
def test_code_below_its_minimum_order_says_how_much(channel, send_codes):
    make_rule(channel, "BIG", "BIG500", min_order="500.00")

    assert per_code(send_codes("BIG500")) == [
        ("codes[0].code", "DISCOUNT_MIN_ORDER_NOT_MET", {"code": "BIG500", "min_order_amount": "500.00"})
    ]


@pytest.mark.django_db
def test_expired_code(channel, send_codes):
    rule = make_rule(channel, "OLD")
    yesterday = timezone.now().date() - timedelta(days=1)
    DiscountCode.objects.create(rule=rule, code="OLD10", max_used=100, active_to=yesterday)

    assert per_code(send_codes("OLD10")) == [
        ("codes[0].code", "DISCOUNT_EXPIRED", {"code": "OLD10", "expired_at": yesterday.isoformat()})
    ]


@pytest.mark.django_db
def test_unknown_code_and_another_channels_code_are_not_found(channel, other_channel, send_codes):
    make_rule(other_channel, "ELSEWHERE", "ELSEWHERE10")

    assert per_code(send_codes("NO_SUCH_CODE", "ELSEWHERE10")) == [
        ("codes[0].code", "DISCOUNT_CODE_NOT_FOUND", {"code": "NO_SUCH_CODE"}),
        ("codes[1].code", "DISCOUNT_CODE_NOT_FOUND", {"code": "ELSEWHERE10"}),
    ]


@pytest.mark.django_db
@pytest.mark.usefixtures("gratis_code")
def test_place_counts_only_the_codes_sent_when_a_gratis_code_exists(send_codes):
    """The gratis probe puts each gratis code ahead of the cart's codes while the cart is validated."""
    assert per_code(send_codes("NO_SUCH_CODE")) == [
        ("codes[0].code", "DISCOUNT_CODE_NOT_FOUND", {"code": "NO_SUCH_CODE"})
    ]


def rejected(code, reason, meta):
    return ValidatedDiscountData(
        code=code,
        sku=None,
        quantity=None,
        clear_discounts=False,
        item_code=None,
        price_discount=0,
        percent_discount=0,
        free_shipping=False,
        free_shipping_methods=[],
        status=ItemStatus.INVALID,
        free_order=False,
        min_order_amount=0,
        extra_value=0,
        target=None,
        modifier=None,
        rejection_reason=reason,
        rejection_meta=meta,
    )


def test_meta_keeps_rule_internals_out():
    """No priority, no rule names the client can't already see in its own cart."""
    error = DiscountsRejectedError(
        message="Some discounts are invalid",
        code="one_or_more_discount_invalid",
        rejected=[
            rejected(
                "A", "blocked_by_exclusive_rule", {"code": "A", "blocking_code": "HIDDEN", "blocking_priority": 1}
            ),
            rejected("B", "blocked_by_price_rules", {"reason": "price_tuner"}),
            rejected("C", None, None),
        ],
        sent_codes=["A", "B", "C"],
        applied_codes=[],
    )

    assert [(e["code"], e["meta"]) for e in map_discount_errors(error)] == [
        ("DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE", {"code": "A"}),
        ("DISCOUNT_BLOCKED_BY_PRICE_RULES", {"code": "B"}),
        ("DISCOUNT_CODE_INVALID", {"code": "C"}),
    ]


@pytest.mark.django_db
def test_v1_error_is_unchanged(channel, coffee_cart):
    make_rule(channel, "BIG", "BIG500", min_order="500.00")

    _, messages = cart_service.patch_discounts(
        channel=channel, cart_id=str(coffee_cart.cart_id), codes=[{"code": "BIG500"}]
    )
    errors, _ = prepare_errors_and_messages(messages)

    assert [e.to_dict() for e in errors if e.code == "one_or_more_discount_invalid"] == [
        {
            "message": "Some discounts are invalid",
            "code": "one_or_more_discount_invalid",
            "affected_values": ["BIG500"],
            "affected_field": "cart.discounts.code",
        }
    ]
