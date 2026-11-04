# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""CHEAPEST_GRATIS and MOST_EXPENSIVE_GRATIS give the chosen item at the gift price.

Both rules computed the discount on the item and then lost it: the write-back after the modifier ``match`` set
``item.discount_amount`` from a local taken before the branch, so the code came out valid with a discount of 0.

The item is picked by its price among the bought lines: a gift line (priced at the gift price, so always the
cheapest) is never picked, and the line with more units no longer wins over the cheaper one.
"""

from decimal import Decimal

import pytest

from django_checkout.models import DiscountCode, DiscountModeOfAction, DiscountRuleCode, ModifiersForDiscountRule
from django_checkout.models.channel import DiscountApplyType
from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType
from django_checkout.services import cart_service
from django_checkout.worker import discount_worker
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _stock,
    catalog,
    coffee_in_stock,
    gratis_code,
    gratis_rule,
    pim_channel,
    priced_catalog,
    supplier,
)

PEN_SKU = "ENT-PEN"  # 5.00 gross, the cheapest product
COFFEE, PEN = Decimal("123.00"), Decimal("5.00")


@pytest.fixture
def shop(channel, supplier, catalog, priced_catalog, pim_channel, coffee_in_stock):  # noqa: F811
    """Coffee (123.00), mugs (24.60) and pens (5.00), priced at 23 % VAT and in stock."""
    from django_pim.models import FeatureSet, Product, RealProduct
    from django_pricemanager.models import Price, TaxClass, TaxRate
    from django_pricemanager.models import ProductRepresentation as PricedProduct

    catalog[PEN_SKU] = Product.objects.create(
        real_product=RealProduct.objects.create(sku=PEN_SKU), shop=pim_channel, feature_set=FeatureSet.objects.first()
    )
    tax_class = TaxClass.objects.get(idx="standard")
    Price.objects.create(
        pricelist=priced_catalog,
        product=PricedProduct.objects.create(tax_class=tax_class, sku=PEN_SKU),
        net_value=Decimal("4.07"),
        gross_value=PEN,
        tax_rate=TaxRate.objects.get(tax_class=tax_class),
    )
    _stock(channel, supplier, PEN_SKU, quantity=10)
    _stock(channel, supplier, GIFT_SKU, quantity=10)
    return channel


def _code(channel, catalog, modifier, code, units=1):  # noqa: F811
    """A rule over every product of the shop, ``units`` of the chosen product at the gift price."""
    rule = DiscountRuleCode.objects.create(
        name=code, modifier=modifier, extra_value=units, combine_with_other_rules=True
    )
    rule.channels.add(channel)
    mode_of_action = DiscountModeOfAction.objects.create(
        rule=rule, is_inclusion_or_exclusion=DiscountModeOfActionType.INCLUSION
    )
    mode_of_action.products.add(*catalog.values())
    return DiscountCode.objects.create(rule=rule, code=code, max_used=100)


@pytest.fixture
def cheapest(shop, catalog):  # noqa: F811
    return _code(shop, catalog, ModifiersForDiscountRule.CHEAPEST_GRATIS, "CHEAPEST_FREE")


@pytest.fixture
def most_expensive(shop, catalog):  # noqa: F811
    return _code(shop, catalog, ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS, "EXPENSIVE_FREE")


def _cart(channel, items, *codes):
    record, _ = cart_service.create_cart(
        channel=channel,
        items=[{"sku": sku, "quantity": quantity} for sku, quantity in items],
        currency_code="EUR",
        language_code="en",
        country_code="PL",
    )
    record, _ = cart_service.patch_discounts(channel=channel, cart_id=str(record.cart_id), codes=list(codes))
    return record.as_data.cart


def _discounts(cart) -> list[tuple[str, bool, Decimal]]:
    return [(item.sku, bool(item.is_gratis), item.discount_amount) for item in cart.items]


@pytest.mark.django_db
class TestTheChosenItemComesAtTheGiftPrice:
    def test_cheapest_gratis_discounts_the_cheapest_item(self, shop, cheapest):
        """1 % of 5.00 is below GRATIS_PRICE (1.00), so the pen costs 1.00."""
        cart = _cart(shop, [(TRIGGER_SKU, 1), (PEN_SKU, 1)], {"code": "CHEAPEST_FREE"})

        assert _discounts(cart) == [(TRIGGER_SKU, False, Decimal("0.00")), (PEN_SKU, False, Decimal("4.00"))]
        assert cart.discount_amount == Decimal("4.00")
        assert cart.total_price == COFFEE + Decimal("1.00")
        assert [d.code for d in cart.discounts] == ["CHEAPEST_FREE"]

    def test_most_expensive_gratis_discounts_the_most_expensive_item(self, shop, most_expensive):
        """1 % of 123.00 is 1.23, above GRATIS_PRICE: the coffee costs 1.23."""
        cart = _cart(shop, [(TRIGGER_SKU, 1), (PEN_SKU, 1)], {"code": "EXPENSIVE_FREE"})

        assert _discounts(cart) == [(TRIGGER_SKU, False, Decimal("121.77")), (PEN_SKU, False, Decimal("0.00"))]
        assert cart.total_price == Decimal("1.23") + PEN

    def test_only_the_rules_units_come_at_the_gift_price(self, shop, cheapest):
        cart = _cart(shop, [(TRIGGER_SKU, 1), (PEN_SKU, 3)], {"code": "CHEAPEST_FREE"})

        assert _discounts(cart)[1] == (PEN_SKU, False, Decimal("4.00"))
        assert cart.total_price == COFFEE + 2 * PEN + Decimal("1.00")

    def test_the_units_are_given_once_across_the_lines_of_the_sku(self, shop, cheapest):
        """Two engraved pens are two lines of one SKU: the rule's one unit goes to one of them."""
        record, _ = cart_service.create_cart(
            channel=shop,
            items=[
                {"sku": TRIGGER_SKU, "quantity": 1},
                {"sku": PEN_SKU, "quantity": 1, "extra": {"engraving": "A"}},
                {"sku": PEN_SKU, "quantity": 1, "extra": {"engraving": "B"}},
            ],
            currency_code="EUR",
            language_code="en",
            country_code="PL",
        )
        record, _ = cart_service.patch_discounts(shop, str(record.cart_id), [{"code": "CHEAPEST_FREE"}])

        cart = record.as_data.cart
        assert [(item.sku, item.discount_amount) for item in cart.items] == [
            (TRIGGER_SKU, Decimal("0.00")),
            (PEN_SKU, Decimal("4.00")),
            (PEN_SKU, Decimal("0.00")),
        ]

    def test_the_cheaper_item_wins_over_the_one_with_more_units(self, shop, cheapest):
        """The pick sorted by quantity last, so quantity decided: three coffees made the coffee 'the cheapest'."""
        cart = _cart(shop, [(TRIGGER_SKU, 3), (PEN_SKU, 1)], {"code": "CHEAPEST_FREE"})

        assert _discounts(cart) == [(TRIGGER_SKU, False, Decimal("0.00")), (PEN_SKU, False, Decimal("4.00"))]

    def test_a_channel_discounting_net_prices_gets_the_same_gift_price(self, shop, cheapest):
        shop.discount_apply_type = DiscountApplyType.BEFORE
        shop.save()

        cart = _cart(shop, [(TRIGGER_SKU, 1), (PEN_SKU, 1)], {"code": "CHEAPEST_FREE"})

        pen = cart.items[1]
        assert pen.discount_amount_netto == Decimal("3.25")  # 4.00 / 1.23
        assert pen.discount_amount == Decimal("4.00")

    def test_a_fixed_gift_price_is_honoured(self, shop, cheapest, monkeypatch):
        """GRATIS_MECHANISM 2 prices every gift at GRATIS_PRICE, here 0: the pen is free."""
        monkeypatch.setattr(discount_worker, "GRATIS_MECHANISM", 2)
        monkeypatch.setattr(discount_worker, "GRATIS_PRICE", 0)

        cart = _cart(shop, [(TRIGGER_SKU, 1), (PEN_SKU, 1)], {"code": "CHEAPEST_FREE"})

        assert _discounts(cart)[1] == (PEN_SKU, False, PEN)
        assert cart.total_price == COFFEE


@pytest.mark.django_db
class TestAGiftLineIsNeverPicked:
    """A gift line costs the gift price, so it always looked like the cheapest item in the cart."""

    def test_cheapest_gratis_skips_the_gift_of_a_bought_sku(self, shop, cheapest, gratis_code, gratis_rule):  # noqa: F811
        gratis_rule.combine_with_other_rules = True
        gratis_rule.save()

        cart = _cart(
            shop,
            [(TRIGGER_SKU, 1), (GIFT_SKU, 1), (PEN_SKU, 1)],
            {"code": "CHEAPEST_FREE"},
            {"code": "ENT-MUG", "sku": GIFT_SKU, "quantity": 1},
        )

        bought_mug, pen, gift = cart.items[1:]
        assert (bought_mug.sku, bought_mug.is_gratis, bought_mug.discount_amount) == (GIFT_SKU, False, Decimal("0.00"))
        assert (pen.sku, pen.discount_amount) == (PEN_SKU, Decimal("4.00"))
        assert (gift.sku, gift.is_gratis, gift.total_price) == (GIFT_SKU, True, Decimal("1.00"))
        assert cart.total_price == COFFEE + Decimal("24.60") + Decimal("1.00") + Decimal("1.00")


def test_the_gift_price_never_raises_a_price():
    """An item cheaper than GRATIS_PRICE keeps its price: the discount is 0, never negative."""
    item = type("Item", (), {"unit_price": Decimal("0.50"), "tax_rate": Decimal("0.23")})()

    assert discount_worker.calculate_discount_when_gratis(item, 1) == Decimal("0.00")
