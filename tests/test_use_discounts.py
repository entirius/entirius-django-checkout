# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A v1 order counts each applied code on the code row the cart applied it from.

``DiscountCode.code`` is not unique: a shop may put one code on an EUR rule and a PLN rule (``GRATIS_STEP`` in the
test package), or reuse a code in another channel. ``use_discounts`` looked the code up with ``get(code=code)``, so
such an order raised ``MultipleObjectsReturned`` once the order was saved, and the v1 view answered 403
``INVALID_REQUEST`` for an order that existed.
"""

from dataclasses import asdict

import pytest
from django_regional.models import Currency

from django_checkout.domain.dto.discount import ValidatedDiscountData
from django_checkout.enums import ItemStatus
from django_checkout.models import DiscountCode, DiscountRuleCode, ModifiersForDiscountRule, Order, UsedCoupon
from django_checkout.utils import sanitize
from django_checkout.views.order import use_discounts


def gratis_step(channel, iso3):
    rule = DiscountRuleCode.objects.create(
        name=f"STEP_{iso3}", modifier=ModifiersForDiscountRule.GRATIS_STEPPED, extra_value={iso3: {"100": 1}}
    )
    rule.channels.add(channel)
    currency, _ = Currency.objects.get_or_create(iso3=iso3, defaults={"name_en": iso3, "name_pl": iso3})
    rule.currencies.add(currency)
    return DiscountCode.objects.create(rule=rule, code="GRATIS_STEP", max_used=100)


def order_with_the_code(channel, currency_code="EUR"):
    """An order as ``Order.create`` stores it: the cart's validated discounts, sanitised."""
    applied = ValidatedDiscountData(
        code="GRATIS_STEP",
        sku=None,
        quantity=None,
        clear_discounts=False,
        item_code=None,
        price_discount=0,
        percent_discount=0,
        free_shipping=False,
        free_shipping_methods=[],
        status=ItemStatus.VALID,
        free_order=False,
        min_order_amount=0.0,
        extra_value={currency_code: {"100": 1}},
        target="all",
        modifier=ModifiersForDiscountRule.GRATIS_STEPPED,
    )
    body = {"currency_code": currency_code, "cart": {"discounts": [sanitize(asdict(applied))]}}
    return Order.objects.create(channel=channel, order_body=body, billing_email="buyer@novatrade.test")


def used(*codes):
    return [DiscountCode.objects.get(pk=code.pk).current_used for code in codes]


@pytest.mark.django_db
@pytest.mark.parametrize(("currency_code", "counted"), [("EUR", [1, 0]), ("PLN", [0, 1])])
def test_a_code_on_rules_of_two_currencies_counts_on_the_order_currency(channel, currency_code, counted):
    eur, pln = gratis_step(channel, "EUR"), gratis_step(channel, "PLN")
    order = order_with_the_code(channel, currency_code)

    use_discounts(order.order_body["cart"]["discounts"], channel, order=order)

    assert used(eur, pln) == counted
    assert list(UsedCoupon.objects.values_list("discount_code", flat=True)) == ["GRATIS_STEP"]


@pytest.mark.django_db
def test_the_same_code_in_another_channel_is_left_alone(channel, other_channel):
    elsewhere, here = gratis_step(other_channel, "EUR"), gratis_step(channel, "EUR")
    order = order_with_the_code(channel)

    use_discounts(order.order_body["cart"]["discounts"], channel, order=order)

    assert used(elsewhere, here) == [0, 1]
