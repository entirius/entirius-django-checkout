# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A gratis product that is out of stock must not be offered, let alone granted.

Gratis eligibility used to be decided purely from the discount rule's product filters,
so a rule could offer a SKU that no warehouse can ship. The customer picked it, the cart
accepted it, and order creation then failed on the stock check with nothing the customer
could do about it. Stock is now part of the eligibility answer, and a customer with their
own supplier is answered from their own shelves.
"""

from decimal import Decimal

import pytest

from django_checkout.domain.dto.cart import CartData, CartRequest
from django_checkout.domain.dto.discount import ValidatedDiscountData
from django_checkout.domain.dto.item import ValidatedItemData
from django_checkout.domain.quantities import filter_saleable_skus
from django_checkout.enums import ItemStatus
from django_checkout.models import (
    DiscountCode,
    DiscountModeOfAction,
    DiscountRuleCode,
    ModifiersForDiscountRule,
    ProductRepresentation,
    Stock,
    Supplier,
)
from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType
from django_checkout.models.supplier import SupplierCustomer
from django_checkout.services import cart_service
from django_checkout.worker.discount_worker import calculate_and_valid_gratis, get_all_available_gratis_rules

TRIGGER_SKU = "ENT-COFFEE-1KG"
GIFT_SKU = "ENT-MUG-350"


def _stock(channel, supplier, sku, quantity, product=None):
    product = product or ProductRepresentation.objects.create(sku=sku, channel=channel)
    return Stock.objects.create(product=product, supplier=supplier, quantity=quantity)


def _cart_picking_the_gift():
    """A kilo of coffee in the cart and the mug picked as the gratis."""
    cart_data = CartData.factory()
    cart_data.items = [
        ValidatedItemData.invalid_factory(sku=TRIGGER_SKU, status=ItemStatus.VALID, is_gratis=False, quantity=1)
    ]
    cart_data.total_price = Decimal("123.00")
    cart_data.total_netto_price = Decimal("100.00")
    cart_data.discounts = [
        ValidatedDiscountData(
            code="ENT-MUG",
            sku=GIFT_SKU,
            quantity=1,
            clear_discounts=False,
            item_code=None,
            price_discount=None,
            percent_discount=None,
            free_shipping=False,
            free_shipping_methods=None,
            status=ItemStatus.VALID,
            free_order=False,
            min_order_amount=0,
            extra_value={"quantity": 1},
            target=None,
            modifier=ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
        )
    ]
    return cart_data


def _offered_gratis(channel):
    body = CartRequest(
        cart=_cart_picking_the_gift(),
        addresses=None,
        payment_method=None,
        shipping_method=None,
        language_code="en",
        currency_code="EUR",
        country_code="PL",
        custom_order_id=None,
        requested_delivery_date=None,
        split_order=False,
        split_by_feature_idx=None,
        split_by_attr_idx=None,
        original_order_id=None,
        comment=None,
        affiliate_code=None,
        customer_ip=None,
    )
    rules, *_ = get_all_available_gratis_rules(
        body, Decimal("123.00"), channel, None, "buyer@novatrade.test", "PL", "EUR"
    )
    return rules


def _claim_the_gift(channel, customer=None):
    """The real cart flow: a kilo of coffee in the cart, the mug claimed with the gratis code."""
    record, _ = cart_service.create_cart(
        channel=channel,
        items=[{"sku": TRIGGER_SKU, "quantity": 1}],
        currency_code="EUR",
        language_code="en",
        country_code="PL",
        customer=customer,
    )
    record, _ = cart_service.patch_discounts(
        channel=channel,
        cart_id=str(record.cart_id),
        codes=[{"code": "ENT-MUG", "sku": GIFT_SKU, "quantity": 1}],
        customer=customer,
    )
    return record


@pytest.fixture
def supplier(channel):
    return Supplier.objects.create(code="kestrel", name="Kestrel Supply", is_global=True, channel=channel)


@pytest.fixture
def own_warehouse(channel, customer):
    """A supplier that stocks for this one customer only (B2B contract warehouse)."""
    supplier = Supplier.objects.create(code="novatrade-b2b", name="Novatrade B2B", is_global=False, channel=channel)
    SupplierCustomer.objects.create(supplier=supplier, customer=customer)
    return supplier


@pytest.fixture
def gift_only_in_the_b2b_warehouse(channel, supplier, own_warehouse):
    gift = ProductRepresentation.objects.create(sku=GIFT_SKU, channel=channel)
    _stock(channel, supplier, GIFT_SKU, quantity=0, product=gift)
    _stock(channel, own_warehouse, GIFT_SKU, quantity=5, product=gift)
    return gift


@pytest.fixture
def pim_channel(db, channel, default_language, default_currency):
    """django_pim Channel sharing the checkout channel's idx (gratis filters join on it)."""
    from django_pim.models import Channel as PimChannel

    return PimChannel.objects.create(
        idx=channel.idx, name="Novatrade", default_language=default_language, default_currency=default_currency
    )


@pytest.fixture
def catalog(pim_channel):
    """Both SKUs exist in the catalog, keyed by SKU."""
    from django_pim.models import FeatureSet, Product, RealProduct

    feature_set = FeatureSet.objects.create(idx="default", name="Default")
    return {
        sku: Product.objects.create(
            real_product=RealProduct.objects.create(sku=sku), shop=pim_channel, feature_set=feature_set
        )
        for sku in (TRIGGER_SKU, GIFT_SKU)
    }


@pytest.fixture
def priced_catalog(channel, default_country, default_currency, catalog):
    """Pricelist entries, so the gratis rule can quote a price for what it offers."""
    from django_pricemanager.models import Channel as PriceChannel
    from django_pricemanager.models import Price, PriceList, SaleChannel, TaxClass, TaxRate
    from django_pricemanager.models import ProductRepresentation as PricedProduct
    from django_pricemanager.models.pricelist import PriceListStatusEnum

    price_channel = PriceChannel.objects.create(idx=channel.idx, name="Novatrade")
    sale_channel = SaleChannel.objects.create(
        idx=f"{channel.idx}-pl", name="Novatrade PL", channel=price_channel, country=default_country
    )
    pricelist = PriceList.objects.create(
        sale_channel=sale_channel,
        currency=default_currency,
        country=default_country,
        status=PriceListStatusEnum.READY,
    )
    tax_class = TaxClass.objects.create(idx="standard", name="Standard")
    tax_rate = TaxRate.objects.create(tax_class=tax_class, country=default_country, rate=Decimal("0.2300"))
    for sku, gross, net in (
        (TRIGGER_SKU, Decimal("123.00"), Decimal("100.00")),
        (GIFT_SKU, Decimal("24.60"), Decimal("20.00")),
    ):
        Price.objects.create(
            pricelist=pricelist,
            product=PricedProduct.objects.create(tax_class=tax_class, sku=sku),
            net_value=net,
            gross_value=gross,
            tax_rate=tax_rate,
        )
    return pricelist


@pytest.fixture
def coffee_in_stock(channel, supplier):
    """The cart item itself has to be shippable, or it never makes it into the cart."""
    return _stock(channel, supplier, TRIGGER_SKU, quantity=10)


@pytest.fixture
def gratis_rule(channel, catalog):
    """Buy the coffee, pick the mug for free."""
    rule = DiscountRuleCode.objects.create(
        name="Mug with every kilo of coffee",
        modifier=ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
        extra_value={"sku": [TRIGGER_SKU], "quantity": 1},
    )
    rule.channels.add(channel)
    mode_of_action = DiscountModeOfAction.objects.create(
        rule=rule, is_inclusion_or_exclusion=DiscountModeOfActionType.INCLUSION
    )
    mode_of_action.products.add(catalog[GIFT_SKU])
    return rule


@pytest.fixture
def gratis_code(gratis_rule):
    return DiscountCode.objects.create(rule=gratis_rule, code="ENT-MUG", max_used=100)


class TestFilterSaleableSkus:
    def test_sku_with_stock_survives(self, channel, supplier):
        _stock(channel, supplier, GIFT_SKU, quantity=5)

        assert filter_saleable_skus([GIFT_SKU], channel) == [GIFT_SKU]

    def test_sku_with_an_empty_stock_row_is_dropped(self, channel, supplier):
        _stock(channel, supplier, GIFT_SKU, quantity=0)

        assert filter_saleable_skus([GIFT_SKU], channel) == []

    def test_sku_without_any_stock_row_is_dropped(self, channel):
        """No warehouse ever reported this SKU - absence means unavailable, not unlimited."""
        assert filter_saleable_skus([GIFT_SKU], channel) == []

    def test_only_the_stocked_skus_come_back(self, channel, supplier):
        _stock(channel, supplier, GIFT_SKU, quantity=3)
        _stock(channel, supplier, TRIGGER_SKU, quantity=0)

        assert filter_saleable_skus([TRIGGER_SKU, GIFT_SKU], channel) == [GIFT_SKU]


class TestGratisNeedsStock:
    def test_gratis_is_granted_when_the_gift_is_in_stock(self, channel, supplier, gratis_rule):
        _stock(channel, supplier, GIFT_SKU, quantity=5)
        cart_data = _cart_picking_the_gift()

        gratis = calculate_and_valid_gratis(gratis_rule, cart_data, channel=channel, discount_idx=0)

        assert gratis == {"sku": GIFT_SKU, "quantity": Decimal(1)}
        assert cart_data.discounts[0].status == ItemStatus.VALID

    def test_gratis_is_refused_when_the_gift_is_out_of_stock(self, channel, supplier, gratis_rule):
        """Same cart, same rule - only the warehouse is empty."""
        _stock(channel, supplier, GIFT_SKU, quantity=0)
        cart_data = _cart_picking_the_gift()

        gratis = calculate_and_valid_gratis(gratis_rule, cart_data, channel=channel, discount_idx=0)

        assert gratis is None
        assert cart_data.discounts[0].status == ItemStatus.INVALID

    def test_gratis_is_refused_when_the_gift_has_no_stock_row_at_all(self, channel, supplier, gratis_rule):
        cart_data = _cart_picking_the_gift()

        gratis = calculate_and_valid_gratis(gratis_rule, cart_data, channel=channel, discount_idx=0)

        assert gratis is None
        assert cart_data.discounts[0].status == ItemStatus.INVALID

    def test_the_customers_own_warehouse_counts(self, channel, gift_only_in_the_b2b_warehouse, customer, gratis_rule):
        """Empty on the global shelf, stocked on the customer's own - they still get the mug."""
        cart_data = _cart_picking_the_gift()

        gratis = calculate_and_valid_gratis(gratis_rule, cart_data, channel=channel, discount_idx=0, customer=customer)

        assert gratis == {"sku": GIFT_SKU, "quantity": Decimal(1)}

    def test_someone_elses_warehouse_does_not_count(self, channel, gift_only_in_the_b2b_warehouse, gratis_rule):
        """Same shelves, no customer: only the global supplier is in scope, and it is empty."""
        cart_data = _cart_picking_the_gift()

        gratis = calculate_and_valid_gratis(gratis_rule, cart_data, channel=channel, discount_idx=0)

        assert gratis is None
        assert cart_data.discounts[0].status == ItemStatus.INVALID


class TestGratisIsNotOffered:
    def test_the_rule_offers_the_gift_when_it_is_in_stock(self, channel, supplier, gratis_code, priced_catalog):
        _stock(channel, supplier, GIFT_SKU, quantity=5)

        offered = _offered_gratis(channel)

        assert [item["sku"] for rule in offered for item in rule["items"]] == [GIFT_SKU]

    def test_an_out_of_stock_gift_is_not_offered_at_all(self, channel, supplier, gratis_code, priced_catalog):
        """The rule has nothing shippable left to give, so it drops out of the list."""
        _stock(channel, supplier, GIFT_SKU, quantity=0)

        assert _offered_gratis(channel) == []


class TestTheCartFlowPassesTheCustomerDown:
    """The stock check only sees a customer if the cart pipeline hands one down.

    ``TestGratisNeedsStock`` calls ``calculate_and_valid_gratis`` directly; these two go
    through ``cart_service`` so a caller that stops forwarding the customer is caught.
    """

    def test_a_gift_from_the_customers_own_warehouse_reaches_the_cart(
        self, channel, customer, gift_only_in_the_b2b_warehouse, gratis_code, priced_catalog, coffee_in_stock
    ):
        record = _claim_the_gift(channel, customer=customer)

        assert [item.sku for item in record.as_data.cart.items if item.is_gratis] == [GIFT_SKU]

    def test_the_same_cart_without_a_customer_gets_no_gift(
        self, channel, customer, gift_only_in_the_b2b_warehouse, gratis_code, priced_catalog, coffee_in_stock
    ):
        """Same shelves, same code - only the global supplier is in scope, and it is empty."""
        record = _claim_the_gift(channel)

        assert [item.sku for item in record.as_data.cart.items] == [TRIGGER_SKU]
