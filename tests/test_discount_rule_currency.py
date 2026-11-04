# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A rule limited to some currencies applies only to carts in one of them; a rule with none applies in every one.

Only the gratis offer list read ``DiscountRuleCode.currencies``. The cart pipeline (automatic rules, codes, priority
arbitration) ignored it, so a PLN-only automatic 5 % discounted an EUR cart, and a PLN-only rule won arbitration on
an EUR cart and pushed the cart's own rules out. The coffee cart below is EUR, 123.00 gross.
"""

import pytest
from django_regional.models import Currency

from django_checkout.models import DiscountCode, DiscountModeOfAction, DiscountRuleCode, ModifiersForDiscountRule
from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType
from django_checkout.services import cart_service
from django_checkout.views.cart import prepare_errors_and_messages
from tests.test_discount_rejection_reasons_v2 import coffee_cart, make_rule, per_code, send_codes  # noqa: F401
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _stock,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)


def only_in(rule, *iso3s):
    for iso3 in iso3s:
        currency, _ = Currency.objects.get_or_create(iso3=iso3, defaults={"name_en": iso3, "name_pl": iso3})
        rule.currencies.add(currency)
    return rule


def eur_cart(channel):
    record, _ = cart_service.create_cart(
        channel=channel, items=[{"sku": TRIGGER_SKU, "quantity": 1}], currency_code="EUR", country_code="PL"
    )
    return record.as_data.cart


def applied(cart):
    return sorted(discount.code for discount in cart.discounts if discount.status == "valid")


@pytest.mark.django_db
@pytest.mark.usefixtures("priced_catalog", "coffee_in_stock")
def test_an_automatic_rule_applies_only_in_its_currencies(channel):
    only_in(make_rule(channel, "AUTO_PLN", automatic=True), "PLN")
    only_in(make_rule(channel, "AUTO_EUR", automatic=True), "EUR")
    only_in(make_rule(channel, "AUTO_BOTH", automatic=True), "PLN", "EUR")
    make_rule(channel, "AUTO_ANY", automatic=True)

    cart = eur_cart(channel)

    assert applied(cart) == ["AUTO_ANY", "AUTO_BOTH", "AUTO_EUR"]


@pytest.mark.django_db
def test_a_code_of_another_currency_is_rejected_as_currency_not_supported(channel, send_codes):  # noqa: F811
    only_in(make_rule(channel, "PLN_ONLY", "PLN10"), "PLN")

    assert per_code(send_codes("PLN10")) == [
        ("codes[0].code", "DISCOUNT_CURRENCY_NOT_SUPPORTED", {"code": "PLN10", "currency": "EUR"})
    ]


@pytest.mark.django_db
def test_a_code_on_rules_of_two_currencies_is_judged_by_the_cart_currency_rule(channel, send_codes):  # noqa: F811
    """The EUR rule needs 500.00; the PLN rule under the same code has no minimum and must not stand in for it."""
    only_in(make_rule(channel, "TWIN_EUR", "TWIN", min_order="500.00"), "EUR")
    only_in(make_rule(channel, "TWIN_PLN", "TWIN"), "PLN")

    assert per_code(send_codes("TWIN")) == [
        ("codes[0].code", "DISCOUNT_MIN_ORDER_NOT_MET", {"code": "TWIN", "min_order_amount": "500.00"})
    ]


@pytest.mark.django_db
def test_v1_lists_a_code_of_another_currency_as_invalid(channel, coffee_cart):  # noqa: F811
    only_in(make_rule(channel, "PLN_ONLY", "PLN10"), "PLN")

    record, messages = cart_service.patch_discounts(
        channel=channel, cart_id=str(coffee_cart.cart_id), codes=[{"code": "PLN10"}]
    )
    errors, _ = prepare_errors_and_messages(messages)

    assert applied(record.as_data.cart) == []
    assert [e.affected_values for e in errors if e.code == "one_or_more_discount_invalid"] == [["PLN10"]]


@pytest.mark.django_db
def test_an_exclusive_rule_of_another_currency_does_not_win_arbitration(channel, send_codes):  # noqa: F811
    only_in(make_rule(channel, "AUTO_PLN_EXCLUSIVE", priority=1, combinable=False, automatic=True), "PLN")
    make_rule(channel, "EUR_CODE", "EUR10", priority=10, combinable=False)

    assert send_codes("EUR10") == []


@pytest.fixture
def gratis_step_in_two_currencies(channel, catalog, supplier):  # noqa: F811
    """GRATIS_STEP as the seed has it: one code on an EUR rule and on a PLN rule; the PLN one ranks first."""
    _stock(channel, supplier, GIFT_SKU, quantity=5)
    for iso3, priority in (("EUR", 10), ("PLN", 5)):
        rule = DiscountRuleCode.objects.create(
            name=f"STEP_{iso3}",
            modifier=ModifiersForDiscountRule.GRATIS_STEPPED,
            extra_value={iso3: {"100": 1}},
            priority=priority,
        )
        rule.channels.add(channel)
        only_in(rule, iso3)
        DiscountCode.objects.create(rule=rule, code="GRATIS_STEP", max_used=100)
        moa = DiscountModeOfAction.objects.create(
            rule=rule, is_inclusion_or_exclusion=DiscountModeOfActionType.INCLUSION
        )
        moa.products.add(catalog[GIFT_SKU])


@pytest.mark.django_db
@pytest.mark.usefixtures("priced_catalog", "coffee_in_stock", "gratis_step_in_two_currencies")
def test_a_code_on_rules_of_two_currencies_offers_the_gift_of_the_cart_currency(channel):
    cart = eur_cart(channel)

    assert [(rule["name"], rule["max_available_quantity"]) for rule in cart.available_gratis_rules or []] == [
        ("STEP_EUR", 1)
    ]


@pytest.mark.django_db
@pytest.mark.usefixtures("priced_catalog", "coffee_in_stock", "gratis_step_in_two_currencies")
def test_a_code_on_rules_of_two_currencies_grants_the_gift_of_the_cart_currency(channel, coffee_cart):  # noqa: F811
    record, messages = cart_service.patch_discounts(
        channel=channel,
        cart_id=str(coffee_cart.cart_id),
        codes=[{"code": "GRATIS_STEP", "sku": GIFT_SKU, "quantity": 1}],
    )
    errors, _ = prepare_errors_and_messages(messages)

    assert [e.code for e in errors if e.code == "one_or_more_discount_invalid"] == []
    assert [item.sku for item in record.as_data.cart.items if item.is_gratis] == [GIFT_SKU]
