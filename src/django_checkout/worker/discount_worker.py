# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import asdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import ExpressionWrapper, F, IntegerField, Q, Value
from django_pim.models import ConfigurableLink, Product

from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.dto.discount import DiscountData, GratisRules
from django_checkout.domain.dto.discount_items import DiscountItems
from django_checkout.domain.dto.item import GratisData, ValidatedItemData
from django_checkout.domain.quantities import filter_saleable_skus
from django_checkout.domain.validators.discounts import validate_discounts
from django_checkout.enums import ItemStatus
from django_checkout.models import (
    DiscountApplyType,
    DiscountRuleCode,
    ModifiersForDiscountRule,
    ProductRepresentation,
    TargetForDiscountRule,
)
from django_checkout.models.abstract_product_filter import PriceRestrictionType
from django_checkout.models.discount_mode_of_actions import DiscountModeOfActionType
from django_checkout.settings import (
    GRATIS_MECHANISM,
    GRATIS_PERCENT_DISCOUNT,
    GRATIS_PRICE,
    INCLUDE_GRATIS_IN_QUANTITY_DISCOUNT_COUNTER,
    SHOW_INVALID_GRATIS,
)
from django_checkout.worker.gratis import calc_base_unit_price_for_gratis


def correct_tax_rounding_error(item: ValidatedItemData) -> ValidatedItemData:
    """
    Koryguje VAT po rabacie używając ROUND_HALF_UP (standard matematyczny).

    Zapewnia spójność z kalkulacją Stripe, który stosuje ROUND_HALF_UP
    przy obliczaniu podatku inclusive.
    """
    if not item.tax_rate or not item.discount_amount:
        return item

    price_after_discount_brutto = item.total_price - item.discount_amount
    divisor = Decimal("1") + item.tax_rate
    netto = (price_after_discount_brutto / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    item.total_tax_amount = price_after_discount_brutto - netto
    item.unit_tax_amount = round(item.total_tax_amount / item.quantity, 2) if item.total_tax_amount else 0

    return item


def filter_items_by_numeric_value(
    cart_data: CartData, field_name: str, min_value: Decimal = None, max_value: Decimal = None
) -> list[str]:
    filtered_skus = []

    for item in cart_data.get_valid_items():
        if isinstance(item, ValidatedItemData):
            field_value = getattr(item, field_name, None)
            if field_value is not None:
                if min_value and max_value:
                    if max_value >= field_value >= min_value:
                        filtered_skus.append(item.sku)
                elif min_value:
                    if min_value <= field_value:
                        filtered_skus.append(item.sku)
                elif max_value:
                    if field_value <= max_value:
                        filtered_skus.append(item.sku)
    return filtered_skus


def filter_cart_by_numeric_value(
    cart_data: CartData, field_name: str, min_value: Decimal = None, max_value: Decimal = None
) -> list[str]:

    cart_price_doesnt_allow_discount = True
    field_value = getattr(cart_data, field_name, None)
    if field_value is not None:
        if min_value and max_value:
            if max_value >= field_value >= min_value:
                cart_price_doesnt_allow_discount = False
        elif min_value:
            if min_value <= field_value:
                cart_price_doesnt_allow_discount = False
        elif max_value:
            if field_value <= max_value:
                cart_price_doesnt_allow_discount = False

    return cart_price_doesnt_allow_discount


def filter_cart_by_specify_value(min_value: Decimal = None, max_value: Decimal = None, value: Decimal = None) -> bool:
    cart_doesnt_allow_discount = False
    if value:
        if min_value and max_value:
            if not max_value >= value >= min_value:
                cart_doesnt_allow_discount = True
        elif min_value:
            if not min_value <= value:
                cart_doesnt_allow_discount = True
        elif max_value:
            if not value <= max_value:
                cart_doesnt_allow_discount = True
    return cart_doesnt_allow_discount


def apply_discount_rule(
    rule: DiscountRuleCode,
    cart_data: CartData | DiscountItems,
    discount_idx: int = 0,
    skip_gratis=False,
    channel=None,
    exclude_skus: set = None,
    currency_code=None,
) -> CartData | DiscountItems:
    rule_obj = DiscountRuleCode.objects.filter(id=rule.id).prefetch_related("discount_mode_of_actions_rule").first()
    filtered_skus = filter_by_inclusion_and_exclusion(rule_obj, cart_data, channel=channel)
    if exclude_skus:
        filtered_skus = filtered_skus.exclude(real_product__sku__in=exclude_skus)
    if not filtered_skus.exists():
        cart_data.discounts[discount_idx].status = ItemStatus.INVALID
        return cart_data
    cart_data = calculate_discounts_modifiers(
        rule_obj, cart_data, filtered_skus, discount_idx, channel, skip_gratis=skip_gratis, currency_code=currency_code
    )
    return cart_data


def filter_by_inclusion_and_exclusion(
    discount_rule,
    cart_data: CartData | DiscountItems,
    channel=None,
    filter_relation_name="discount_mode_of_actions_rule",
):
    sku_list_in_cart = []
    if discount_rule.modifier in [
        ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
        ModifiersForDiscountRule.GRATIS_STEPPED,
    ]:
        products_eligible_for_discount = Product.objects.all()
    else:
        sku_list_in_cart = [item.sku for item in cart_data.get_valid_items()]
        sku_list_in_cart = ProductRepresentation.objects.filter(sku__in=sku_list_in_cart).values_list("sku", flat=True)
        products_eligible_for_discount = Product.objects.filter(real_product__sku__in=sku_list_in_cart)
    if channel:
        products_eligible_for_discount = products_eligible_for_discount.filter(shop__idx=channel.idx)
    query_filter = []
    mode_of_action_is_none = []
    is_for_all = discount_rule.target not in [
        TargetForDiscountRule.FIRST_ORDER_LOGGED,
        TargetForDiscountRule.FIRST_ORDER_ALL,
    ]
    for mode_of_action_type in [DiscountModeOfActionType.INCLUSION, DiscountModeOfActionType.EXCLUSION]:
        for take_common_part in [True, False]:
            main_query_filter_list = []
            filter_relation = getattr(discount_rule, filter_relation_name)
            mode_of_actions = filter_relation.filter(
                is_inclusion_or_exclusion=mode_of_action_type, take_common_part=take_common_part
            )
            if not mode_of_actions.exists():
                mode_of_action_is_none.append(True)
                continue
            product_skus = mode_of_actions.exclude(products__real_product__sku=None).values_list(
                "products__real_product__sku", flat=True
            )
            category_idxs = (
                mode_of_actions.prefetch_related(filter_relation_name)
                .filter(categories__idx__isnull=False)
                .values_list("categories__idx", flat=True)
            ).exclude(categories__idx=None)

            attribute_pks = (
                mode_of_actions.prefetch_related(filter_relation_name)
                .filter(attributes__pk__isnull=False)
                .values_list("attributes__pk", flat=True)
            ).exclude(attributes__pk=None)

            features_qty_greater_than_attr_value_idxs = (
                mode_of_actions.prefetch_related(filter_relation_name)
                .filter(features_qty_greater_than_attr_value__idx__isnull=False)
                .values_list("features_qty_greater_than_attr_value__idx", flat=True)
            ).exclude(features_qty_greater_than_attr_value__idx=None)

            features_qty_is_multiple_of_attr_value_idxs = (
                mode_of_actions.prefetch_related(filter_relation_name)
                .filter(features_qty_is_multiple_of_attr_value__idx__isnull=False)
                .values_list("features_qty_is_multiple_of_attr_value__idx", flat=True)
            ).exclude(features_qty_is_multiple_of_attr_value__idx=None)

            # skusy uwzlęgdnione/wykluczone przy rabacie
            if product_skus.exists() and product_skus:
                product_skus_exists = True

                # jeżeli podamy config, wszystkie dzieci również mają być uwzlęgdnione/wykluczone przy rabacie
                config_children_sku = ConfigurableLink.objects.filter(
                    product_configurable__real_product__sku__in=product_skus
                ).values_list("subproduct__real_product__sku", flat=True)
                if config_children_sku:
                    main_query_filter_list.append(
                        Q(real_product__sku__in=[*config_children_sku, *product_skus])
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                        else ~Q(real_product__sku__in=[*config_children_sku, *product_skus])
                    )
                else:
                    main_query_filter_list.append(
                        Q(real_product__sku__in=product_skus)
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                        else ~Q(real_product__sku__in=product_skus)
                    )

            # kategorie uwzlęgdnione/wykluczone przy rabacie
            if category_idxs.exists():
                main_query_filter_list.append(
                    Q(categories__idx__in=category_idxs)
                    if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                    else ~Q(categories__idx__in=category_idxs)
                )

            # attrybuty uwzlęgdnione/wykluczone przy rabacie
            if attribute_pks.exists():
                main_query_filter_list.append(
                    Q(products_attributes__attribute__pk__in=attribute_pks)
                    if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                    else ~Q(products_attributes__attribute__pk__in=attribute_pks)
                )

            features_qty_is_multiple_of_attr_value_filter_list = Q()
            if features_qty_is_multiple_of_attr_value_idxs.exists():
                if is_for_all:
                    for product in cart_data.get_valid_items():
                        products_egible = (
                            Product.objects.filter(
                                products_attributes__feature__idx__in=features_qty_is_multiple_of_attr_value_idxs,
                                products_attributes__product__real_product__sku=product.sku,
                                products_attributes__product__shop__idx=channel.idx,
                                products_attributes__value_decimal__gt=0,  # avoid division by zero
                            )
                            .annotate(
                                mod=ExpressionWrapper(
                                    Value(product.quantity) % F("products_attributes__value_decimal"),
                                    output_field=IntegerField(),
                                )
                            )
                            .filter(mod=0)
                            .values_list("real_product__sku", flat=True)
                        )

                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION:
                            features_qty_is_multiple_of_attr_value_filter_list |= Q(
                                real_product__sku__in=products_egible
                            )
                        else:
                            features_qty_is_multiple_of_attr_value_filter_list |= ~Q(
                                real_product__sku__in=products_egible
                            )
                else:
                    products_eligible_for_discount = Product.objects.none()

            if features_qty_is_multiple_of_attr_value_filter_list:
                main_query_filter_list.append(features_qty_is_multiple_of_attr_value_filter_list)

            # attrybuty uwzlęgdnione/wykluczone przy rabacie
            features_qty_greater_than_attr_value_filter_list = Q()
            if features_qty_greater_than_attr_value_idxs.exists():
                if is_for_all:
                    for product in cart_data.get_valid_items():
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION:
                            features_qty_greater_than_attr_value_filter_list |= Q(
                                products_attributes__feature__idx__in=features_qty_greater_than_attr_value_idxs,
                                products_attributes__value_decimal__lt=product.quantity,
                                products_attributes__product__real_product__sku=product.sku,
                                products_attributes__product__shop__idx=channel.idx,
                                products_attributes__value_decimal__gt=0,
                            )
                        else:
                            features_qty_greater_than_attr_value_filter_list |= ~Q(
                                products_attributes__feature__idx__in=features_qty_greater_than_attr_value_idxs,
                                products_attributes__value_decimal__lt=product.quantity,
                                products_attributes__product__real_product__sku=product.sku,
                                products_attributes__product__shop__idx=channel.idx,
                                products_attributes__value_decimal__gt=0,
                            )

                else:
                    products_eligible_for_discount = Product.objects.none()

            if features_qty_greater_than_attr_value_filter_list:
                main_query_filter_list.append(features_qty_greater_than_attr_value_filter_list)

            cart_price_to = None
            cart_price_from = None
            qty_to = None
            qty_from = None
            cart_qty_from = None
            cart_qty_to = None
            product_price_to = None
            product_price_from = None

            # kod działa/nie-działa dla produktów od/do konkretnej total_price
            mode_of_actions_filter = []
            for mode_of_action in mode_of_actions:
                independence_query_filter = []
                product_price_from = mode_of_action.product_price_from
                product_price_to = mode_of_action.product_price_to
                price_comparison = (
                    "unit_price"
                    if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
                    else "unit_price_netto"
                )
                if product_price_to or product_price_from:
                    total_price_sku_filtered = filter_items_by_numeric_value(
                        cart_data, price_comparison, product_price_from, product_price_to
                    )
                    independence_query_filter.append(
                        Q(real_product__sku__in=total_price_sku_filtered)
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                        else ~Q(real_product__sku__in=total_price_sku_filtered)
                    )

                qty_from = mode_of_action.qty_from
                qty_to = mode_of_action.qty_to
                if qty_to or qty_from:
                    qty_sku_filtered = filter_items_by_numeric_value(cart_data, "quantity", qty_from, qty_to)
                    independence_query_filter.append(
                        Q(real_product__sku__in=qty_sku_filtered)
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION
                        else ~Q(real_product__sku__in=qty_sku_filtered)
                    )

                # kod działa/nie-działa dla konkretnej wartości koszyka od/do
                cart_qty_from = mode_of_action.cart_qty_from
                cart_qty_to = mode_of_action.cart_qty_to
                qty = calculate_items_qty(cart_data, sku_list_in_cart)
                if cart_qty_to or cart_qty_from:
                    cart_qty_doesnt_allow_discount = filter_cart_by_specify_value(
                        cart_qty_from, cart_qty_to, Decimal(qty)
                    )
                    if cart_qty_doesnt_allow_discount:
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION:
                            independence_query_filter.append(Q(real_product__sku__in=[]))

                # kod działa/nie-działa dla konkretnej wartości koszyka od/do
                cart_price_from = mode_of_action.cart_price_from
                cart_price_to = mode_of_action.cart_price_to

                price_comparison = (
                    "total_price"
                    if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
                    else "total_netto_price"
                )

                if cart_price_to or cart_price_from:
                    cart_price_doesnt_allow_discount = filter_cart_by_numeric_value(
                        cart_data, price_comparison, cart_price_from, cart_price_to
                    )
                    if cart_price_doesnt_allow_discount:
                        if mode_of_action_type is DiscountModeOfActionType.INCLUSION:
                            independence_query_filter.append(Q(real_product__sku__in=[]))

                if independence_query_filter:
                    mode_of_actions_filter.append(Q(*independence_query_filter, _connector=Q.AND))

            list_fields = [
                cart_price_to,
                cart_price_from,
                qty_to,
                qty_from,
                cart_qty_from,
                cart_qty_to,
                product_price_to,
                product_price_from,
                attribute_pks.exists(),
                features_qty_is_multiple_of_attr_value_idxs.exists(),
                features_qty_greater_than_attr_value_idxs.exists(),
                category_idxs.exists(),
                product_skus.exists(),
            ]
            is_all_field_blank = all(element in [None, False] for element in list_fields)
            mode_of_action_is_none.append(is_all_field_blank)
            if take_common_part:
                query_filter.append(
                    Q(*main_query_filter_list, _connector=Q.AND) & Q(*mode_of_actions_filter, _connector=Q.AND)
                )
            else:
                query_filter.append(
                    Q(*main_query_filter_list, _connector=Q.OR) & Q(*mode_of_actions_filter, _connector=Q.OR)
                )

    if all(mode_of_action_is_none):
        pass
    else:
        products_eligible_for_discount = products_eligible_for_discount.filter(
            Q(*query_filter, _connector=Q.AND)
        ).distinct()

    if all(mode_of_action_is_none) and discount_rule.modifier in [
        ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
        ModifiersForDiscountRule.GRATIS_STEPPED,
        ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY,
    ]:
        if filter_relation_name == "threshold_filters":
            pass
        else:
            products_eligible_for_discount = Product.objects.none()

    return products_eligible_for_discount


def get_value_for_currency(value, currency_code=None):
    """
    Uniwersalna funkcja do wyciągania wartości dla danej waluty.
    Obsługuje zarówno proste wartości (int/float) jak i słowniki progów.

    Args:
        value: może być:
            - int/float: 10 (stary format - dla wszystkich walut)
            - dict prosty: {"EUR": 10, "PLN": 15} (nowy format dla PERCENT/PRICE_DISCOUNT)
            - dict progów: {"1": 10, "2": 20} (stary format dla STEP_*)
            - dict progów per waluta: {"EUR": {"1": 10}, "PLN": {"1": 15}} (nowy format dla STEP_*)
            - list: ["SKU1", "SKU2"] (dla GRATIS_BY_SKU_IN_CART - nie dotyczy walut)
        currency_code (str, optional): kod waluty (np. "EUR", "PLN")

    Returns:
        Wartość dla danej waluty lub None jeśli waluta nie jest obsługiwana w nowym formacie
        W starym formacie zwraca wartość niezależnie od waluty (backward compatibility)
    """
    if isinstance(value, (int, float)):
        return value

    if isinstance(value, list):
        return value

    if isinstance(value, dict):
        if not value:
            return None

        first_value = next(iter(value.values()))

        if isinstance(first_value, dict):
            if currency_code and currency_code in value:
                return value[currency_code]
            else:
                return None
        else:
            first_key = next(iter(value.keys()))

            if isinstance(first_key, str) and len(first_key) == 3 and first_key.isupper() and first_key.isalpha():
                if currency_code and currency_code in value:
                    return value[currency_code]
                else:
                    return None  # Waluta nie obsługiwana
            else:
                return value

    return None


def get_thresholds_for_currency(thresholds_config, currency_code=None):
    """
    Rozpoznaje format konfiguracji progów i zwraca odpowiednie progi dla waluty.

    DEPRECATED: Użyj get_value_for_currency () zamiast tej funkcji.
    Ta funkcja jest zachowana dla backward compatibility.

    Args:
        thresholds_config (dict): Konfiguracja progów-może być w formacie:
            - Stary format (wszystkie waluty): {"100": 1, "200": 2}
            - Nowy format (per waluta): {"EUR": {"20": 1, "50": 2}, "PLN": {"100": 1, "200": 2}}
        currency_code (str, optional): Kod waluty np. "EUR", "PLN"

    Returns:
        dict or None: Słownik progów {"próg": ilość_gratisów} lub None, jeśli waluta nie jest obsługiwana
    """
    return get_value_for_currency(thresholds_config, currency_code)


def get_threshold_value(thresholds, number, reverse=True):
    if not isinstance(thresholds, dict):
        raise Exception("Make sure your extra_value is valid")
    next_threshold = 0
    closest_threshold = None
    next_gratis_tier_quantity = 0
    remaining_to_next_threshold = 0
    sorted_thresholds_max_to_min = sorted(thresholds.keys(), key=float, reverse=True)
    for idx, threshold in enumerate(sorted_thresholds_max_to_min):
        if float(number) >= float(threshold):
            closest_threshold = thresholds[threshold]
            if idx == 0:
                # nie ma więcej progu
                next_threshold = None
                next_gratis_tier_quantity = None
                remaining_to_next_threshold = None
            else:
                # znajdź kolejny próg
                next_threshold = sorted_thresholds_max_to_min[idx - 1]
                next_gratis_tier_quantity = thresholds[sorted_thresholds_max_to_min[idx - 1]]
                remaining_to_next_threshold = float(sorted_thresholds_max_to_min[idx - 1]) - float(number)
            break

    # jeżeli nie ma żadnego progu, to przelicz ile brakuje do najbliższego progu
    if closest_threshold is None:
        next_threshold = sorted_thresholds_max_to_min[-1]
        next_gratis_tier_quantity = thresholds[sorted_thresholds_max_to_min[-1]]
        remaining_to_next_threshold = float(sorted_thresholds_max_to_min[-1]) - float(number)

    return closest_threshold, remaining_to_next_threshold, next_gratis_tier_quantity, next_threshold


def get_fixed_price_for_currency(thresholds_config: dict, number: int, currency_code: str):
    """
    Pobiera ustaloną cenę dla danej waluty na podstawie progów ilościowych.

    Args:
        thresholds_config (dict): Konfiguracja progów np. {"EUR": {"2": 30, "5": 25}, "PLN": {"2": 40, "5": 35}}
        number (int): Ilość produktów w koszyku
        currency_code (str): Kod waluty np. "USD", "EUR"

    Returns:
        Decimal or None: Ustalona cena dla danej waluty i progu ilościowego
    """
    if not isinstance(thresholds_config, dict):
        raise Exception("Make sure your extra_value is valid")

    if currency_code not in thresholds_config:
        return None

    currency_thresholds = thresholds_config[currency_code]
    if not isinstance(currency_thresholds, dict):
        return None

    sorted_thresholds_max_to_min = sorted(currency_thresholds.keys(), key=float, reverse=True)

    for threshold in sorted_thresholds_max_to_min:
        if float(number) >= float(threshold):
            return Decimal(str(currency_thresholds[threshold]))

    return None


def calculate_items_qty(cart_data: CartData | DiscountItems, filtered_skus):
    all_qty = 0
    for item in cart_data.get_valid_items():
        if item.sku in filtered_skus and not getattr(item, "is_gratis", False):
            all_qty += item.quantity
    return all_qty


def calculate_items_total_price(cart_data: CartData | DiscountItems, filtered_skus, channel=None):
    all_price = 0
    for item in cart_data.get_valid_items():
        if item.sku in filtered_skus and not getattr(item, "is_gratis", False):
            if channel and channel.discount_apply_type == DiscountApplyType.BEFORE:
                all_price += item.total_price_netto
            else:
                all_price += item.total_price
    return all_price


def get_item_by_price(cart_data: CartData | DiscountItems, filtered_skus, get_type: str):
    filtered_skus = [
        (item.sku, item.unit_price, item.quantity) for item in cart_data.get_valid_items() if item.sku in filtered_skus
    ]
    sorted_list = None
    sku = None
    if get_type == "lowest":
        sorted_list = sorted(filtered_skus, key=lambda x: x[1])
    elif get_type == "highest":
        sorted_list = sorted(filtered_skus, key=lambda x: x[1], reverse=True)
    sorted_list = sorted(sorted_list, key=lambda x: x[2], reverse=True)
    if sorted_list:
        sku = sorted_list[0][0]
    return sku


def validate_discount_target(discount: DiscountRuleCode, customer):
    pass


def calculate_discount_when_gratis(item, how_many_products_gratis):
    percent_price = round(item.unit_price * Decimal((100 - GRATIS_PERCENT_DISCOUNT) / 100), 2)

    if percent_price > GRATIS_PRICE:
        final_price_gratis = percent_price
    else:
        final_price_gratis = GRATIS_PRICE

    item.discount_amount = item.discount_amount + Decimal(
        round((Decimal(how_many_products_gratis) * item.unit_price), 2)
        - (Decimal(how_many_products_gratis) * final_price_gratis)
    )
    return item


def check_is_gratis_allowed(cart_data, discount):
    all_items = [item.sku for item in cart_data.get_valid_items() if not getattr(item, "is_gratis", False)]
    items_needed_in_cart = discount.extra_value["sku"]
    sku_logic = discount.extra_value.get("sku_logic", "AND").upper()
    if sku_logic == "OR":
        return any(element in all_items for element in items_needed_in_cart)
    return all(element in all_items for element in items_needed_in_cart)


def calculate_discount_when_price_discount(
    filtered_skus,
    cart_data,
    discount,
    price_discount,
    channel,
    discount_amount,
    price_original,
    idx,
    discount_extra_value_rest,
    discount_idx,
    current_item,
):
    items_total_price = calculate_items_total_price(cart_data, filtered_skus, channel)
    price_percentage = price_original / items_total_price if items_total_price else 0
    try:
        full_discount_price = price_discount
    except TypeError:
        raise Exception(f"Make sure your extra_value for discount.pk = {discount.pk} is valid")
    discount_amount = discount_amount + (round((full_discount_price * price_percentage), 2))

    valid_non_gratis_items = [
        item
        for item in cart_data.get_valid_items()
        if str(item.sku) in filtered_skus and not getattr(item, "is_gratis", False)
    ]

    # Jeżeli to ostatni element NIE-gratis, przypisuje resztę kwoty discountu by nie było błędu z zaokrągleniem
    if valid_non_gratis_items and current_item == valid_non_gratis_items[-1]:
        discount_amount = discount_extra_value_rest
    else:
        discount_extra_value_rest -= discount_amount
    cart_data.discounts[discount_idx].price_discount = price_discount
    return cart_data, discount_amount, discount_extra_value_rest


def calculate_discount_when_fixed_price_per_currency(item, fixed_price, channel, discount_amount):
    """
    Oblicza rabat na podstawie ustalonej ceny per waluta.

    Args:
        item: Produkt w koszyku
        fixed_price (Decimal): Ustalona cena za sztukę
        channel: Kanał sprzedaży
        discount_amount (Decimal): Aktualna kwota rabatu

    Returns:
        Decimal: Nowa kwota rabatu
    """
    if fixed_price is None:
        return discount_amount

    # Oblicz różnicę między aktualną ceną a ustaloną ceną za sztukę
    unit_price = (
        item.unit_price_netto
        if channel and channel.discount_apply_type == DiscountApplyType.BEFORE
        else item.unit_price
    )

    if unit_price > fixed_price:
        # Rabat tylko jeśli ustalona cena jest niższa niż aktualna
        unit_discount = unit_price - fixed_price
        total_discount = unit_discount * item.quantity
        discount_amount = discount_amount + Decimal(round(total_discount, 2))

    return discount_amount


def calculate_discounts_modifiers(
    discount: DiscountRuleCode,
    cart_data: CartData | DiscountItems,
    filtered_skus,
    discount_idx: int = 0,
    channel=None,
    skip_gratis=False,
    currency_code=None,
):
    closest_threshold = 0
    filtered_skus = [product.sku for product in filtered_skus]
    if channel and channel.discount_apply_type == DiscountApplyType.BEFORE:
        total_price = cart_data.total_netto_price
    else:
        total_price = cart_data.total_price

    sku_the_cheapest = None
    if discount.modifier == ModifiersForDiscountRule.CHEAPEST_GRATIS:
        sku_the_cheapest = get_item_by_price(cart_data, filtered_skus, "lowest")

    sku_the_most_expensive = None
    if discount.modifier == ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS:
        sku_the_most_expensive = get_item_by_price(cart_data, filtered_skus, "highest")
    cart_data.discounts[discount_idx].item_code = list(set(filtered_skus))

    if discount.modifier in [
        ModifiersForDiscountRule.STEP_QTY_PRICE_DISCOUNT_WHOLE_CART,
        ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART,
    ]:
        thresholds = get_value_for_currency(discount.extra_value, currency_code)
        if thresholds is not None:
            closest_threshold, *_ = get_threshold_value(
                thresholds,
                sum(
                    [
                        item.quantity
                        for item in cart_data.get_valid_items()
                        if not INCLUDE_GRATIS_IN_QUANTITY_DISCOUNT_COUNTER
                        and not getattr(item, "is_gratis", False)
                        and item.sku in filtered_skus
                    ]
                ),
            )
            discount_extra_value_rest = closest_threshold
        else:
            discount_extra_value_rest = None
    elif discount.modifier == ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY:
        # Dla tego typu rabatu nie używamy get_threshold_value
        # bo ma inną strukturę danych (waluta jako klucz główny)
        discount_extra_value_rest = discount.extra_value
    else:
        value_for_currency = get_value_for_currency(discount.extra_value, currency_code)
        if value_for_currency is not None:
            discount_extra_value_rest = (
                round(Decimal(value_for_currency), 2)
                if isinstance(value_for_currency, (int, float))
                else value_for_currency
            )
        else:
            discount_extra_value_rest = None
    discount_independent = 0
    for idx, item in enumerate(cart_data.get_valid_items()):
        is_gratis = getattr(item, "is_gratis", False)
        if str(item.sku) not in filtered_skus:
            continue
        if all([skip_gratis, is_gratis]):
            continue

        # Calculate by channel discount type
        if channel and channel.discount_apply_type == DiscountApplyType.BEFORE:
            price_original = item.total_price_netto
            price_original_with_discount = item.total_price_netto - item.discount_amount_netto
            discount_amount = item.discount_amount_netto
        else:
            # DiscountApplyType.AFTER is default
            price_original = item.total_price
            price_original_with_discount = item.total_price - item.discount_amount
            discount_amount = item.discount_amount

        match discount.modifier:
            case ModifiersForDiscountRule.PERCENT_DISCOUNT:
                percent_value = get_value_for_currency(discount.extra_value, currency_code)
                if percent_value is not None:
                    discount_amount = discount_amount + Decimal(
                        round(Decimal(percent_value / 100) * price_original_with_discount, 2)
                    )
                    cart_data.discounts[discount_idx].percent_discount = percent_value

            case ModifiersForDiscountRule.PRICE_DISCOUNT:
                if discount_extra_value_rest is not None:
                    cart_data, discount_amount, discount_extra_value_rest = calculate_discount_when_price_discount(
                        filtered_skus,
                        cart_data,
                        discount,
                        discount_extra_value_rest,
                        channel,
                        discount_amount,
                        price_original,
                        idx,
                        discount_extra_value_rest,
                        discount_idx,
                        item,
                    )

            case ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART:
                if closest_threshold:
                    discount_amount = discount_amount + Decimal(
                        round(Decimal(closest_threshold / 100) * price_original_with_discount, 2)
                    )
                    cart_data.discounts[discount_idx].percent_discount = closest_threshold

            case ModifiersForDiscountRule.STEP_QTY_PRICE_DISCOUNT_WHOLE_CART:
                if discount_extra_value_rest:
                    cart_data, discount_amount, discount_extra_value_rest = calculate_discount_when_price_discount(
                        filtered_skus,
                        cart_data,
                        discount,
                        closest_threshold,
                        channel,
                        discount_amount,
                        price_original,
                        idx,
                        discount_extra_value_rest,
                        discount_idx,
                        item,
                    )

            case ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY:
                if currency_code:
                    qty_filtered_sku = sum(
                        [
                            item.quantity
                            for item in cart_data.items
                            if not INCLUDE_GRATIS_IN_QUANTITY_DISCOUNT_COUNTER
                            and not getattr(item, "is_gratis", False)
                            and item.sku in filtered_skus
                        ]
                    )
                    fixed_price = get_fixed_price_for_currency(discount.extra_value, qty_filtered_sku, currency_code)
                    if fixed_price is not None:
                        da = calculate_discount_when_fixed_price_per_currency(
                            item, fixed_price, channel, discount_amount
                        )
                        discount_independent += da
                        if not item.special_unit_price:
                            fixed_price_qty = fixed_price * item.quantity
                            item.special_total_price = Decimal(
                                round(fixed_price_qty if fixed_price_qty < item.total_price else item.total_price, 2)
                            )
                            item.special_unit_price = Decimal(
                                round(fixed_price if fixed_price_qty < item.total_price else item.unit_price, 2)
                            )

                            item.special_percent = (
                                float(round((item.total_price - item.special_total_price) / item.total_price * 100, 2))
                                if item.unit_price
                                else 0
                            )
                            item.total_price = Decimal(
                                round(fixed_price_qty if fixed_price_qty < item.total_price else item.total_price, 2)
                            )

            case ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT:
                thresholds = get_value_for_currency(discount.extra_value, currency_code)
                if thresholds is not None:
                    closest_threshold, remaining_to_next_threshold, next_gratis_tier_quantity, _ = get_threshold_value(
                        thresholds, item.quantity
                    )
                    if closest_threshold:
                        discount_amount = discount_amount + Decimal(
                            round(Decimal(closest_threshold / 100) * price_original_with_discount, 2)
                        )
                        cart_data.discounts[discount_idx].percent_discount = closest_threshold

            case ModifiersForDiscountRule.STEP_PRICE_PERCENT_DISCOUNT:
                thresholds = get_value_for_currency(discount.extra_value, currency_code)
                if thresholds is not None:
                    closest_threshold, remaining_to_next_threshold, next_gratis_tier_quantity, _ = get_threshold_value(
                        thresholds, total_price
                    )
                    if closest_threshold:
                        discount_amount = discount_amount + Decimal(
                            round(Decimal(closest_threshold / 100) * price_original_with_discount, 2)
                        )
                        cart_data.discounts[discount_idx].percent_discount = closest_threshold

            case ModifiersForDiscountRule.CHEAPEST_GRATIS:
                gratis_qty = get_value_for_currency(discount.extra_value, currency_code)
                if gratis_qty is not None and sku_the_cheapest:
                    if item.sku == sku_the_cheapest:
                        if item.quantity < gratis_qty:
                            how_many_cheapest_products_gratis = item.quantity
                        else:
                            how_many_cheapest_products_gratis = gratis_qty
                        item = calculate_discount_when_gratis(item, how_many_cheapest_products_gratis)

            case ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS:
                gratis_qty = get_value_for_currency(discount.extra_value, currency_code)
                if gratis_qty is not None and sku_the_most_expensive:
                    if item.sku == sku_the_most_expensive:
                        if item.quantity < gratis_qty:
                            how_many_expensive_products_gratis = item.quantity
                        else:
                            how_many_expensive_products_gratis = gratis_qty
                        item = calculate_discount_when_gratis(item, how_many_expensive_products_gratis)
            case _:
                break

        if channel and channel.discount_apply_type == DiscountApplyType.BEFORE:
            item.discount_amount_netto = round(discount_amount, 2)
            item.discount_percent = (
                float(round(item.discount_amount_netto / item.total_price_netto * 100, 2))
                if item.total_price_netto
                else 0
            )
            item.discount_amount = round(item.discount_amount_netto * (1 + (item.tax_rate or 0)), 2)
            if item.tax_rate:
                item.base_total_tax_amount = item.total_tax_amount
                item.base_unit_tax_amount = item.unit_tax_amount
                item.total_tax_amount = (
                    item.total_tax_amount - (item.discount_amount - item.discount_amount_netto)
                    if item.total_tax_amount
                    else 0
                )
                item.unit_tax_amount = round(item.total_tax_amount / item.quantity, 2) if item.total_tax_amount else 0
                # Koryguj błędy zaokrągleń VAT o 1 grosz
                item = correct_tax_rounding_error(item)
        else:
            item.discount_amount = round(discount_amount, 2)
            item.discount_percent = (
                float(round(item.discount_amount / item.total_price * 100, 2)) if item.total_price else 0
            )
            item.discount_amount_netto = round(item.discount_amount / (1 + (item.tax_rate or 0)), 2)
            if item.tax_rate:
                item.base_total_tax_amount = item.total_tax_amount
                item.base_unit_tax_amount = item.unit_tax_amount
                item.total_tax_amount = (
                    item.total_tax_amount - (item.discount_amount - item.discount_amount_netto)
                    if item.total_tax_amount
                    else 0
                )
                item.unit_tax_amount = round(item.total_tax_amount / item.quantity, 2) if item.total_tax_amount else 0
                # Koryguj błędy zaokrągleń VAT o 1 grosz
                item = correct_tax_rounding_error(item)

    validated_items = cart_data.get_valid_items()

    cart_data.discount_amount = sum([item.discount_amount for item in validated_items if item.discount_amount])
    cart_data.discount_amount_netto = sum(
        item.discount_amount_netto for item in validated_items if item.discount_amount_netto
    )

    if discount.modifier == ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY and discount_independent:
        cart_data.discounts[discount_idx].price_discount = Decimal(round(discount_independent, 2))

    cart_data.total_price = sum(
        [
            (
                item.total_price
                if getattr(item, "is_gratis", False) or not item.discount_amount
                else item.total_price - item.discount_amount
            )
            for item in validated_items
            if item
        ]
    )
    cart_data.total_netto_price = sum(
        [
            (
                item.total_price_netto
                if getattr(item, "is_gratis", False) or not item.discount_amount_netto
                else item.total_price_netto - item.discount_amount_netto
            )
            for item in validated_items
            if item and hasattr(item, "total_price_netto")
        ]
    )
    cart_data.tax_amount = round(abs(cart_data.total_price - cart_data.total_netto_price), 2)
    return cart_data


def calculate_and_valid_gratis(
    discount: DiscountRuleCode, cart_data: CartData, channel=None, discount_idx=0, currency_code=None, customer=None
):
    """Zwraca gratis, jeżeli koszyk się kwalifikuje na podany sku i jego podaną ilość w koszyku"""
    gratis_is_allowed = False
    total_price = (
        cart_data.total_price
        if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
        else cart_data.total_netto_price
    )

    if hasattr(discount, "threshold_filters") and discount.threshold_filters.exists():
        filtered_skus = filter_products_by_threshold_filters(discount, cart_data, channel)

        cart_items = cart_data.get_valid_items() if hasattr(cart_data, "get_valid_items") else []
        if not cart_items and hasattr(cart_data, "items"):
            cart_items = cart_data.items

        all_skus = [item.sku for item in cart_items]

        if not filtered_skus:
            total_price = 0
        elif set(filtered_skus) == set(all_skus):
            pass
        else:
            filtered_total = 0
            filtered_total_netto = 0
            for item in cart_items:
                if item.sku in filtered_skus:
                    if hasattr(item, "total_price") and item.total_price is not None:
                        filtered_total += item.total_price
                        if hasattr(item, "total_price_netto") and item.total_price_netto is not None:
                            filtered_total_netto += item.total_price_netto

            if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO:
                total_price = filtered_total
            else:
                total_price = filtered_total_netto

    products_allowed_to_gratis = filter_by_inclusion_and_exclusion(discount, cart_data, channel)
    if not products_allowed_to_gratis.exists():
        cart_data.discounts[discount_idx].status = ItemStatus.INVALID
        return {}
    skus_allowed_to_gratis = products_allowed_to_gratis.values_list("real_product__sku", flat=True)
    picked_sku = cart_data.discounts[discount_idx].sku
    picked_quantity = cart_data.discounts[discount_idx].quantity
    if picked_sku and not filter_saleable_skus([picked_sku], channel, customer):
        cart_data.discounts[discount_idx].status = ItemStatus.INVALID
        return None
    if discount.modifier == ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART:
        gratis_is_allowed = check_is_gratis_allowed(cart_data, discount)
    match discount.modifier:
        case ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART:
            if picked_sku in skus_allowed_to_gratis:
                if gratis_is_allowed:
                    if 0 < picked_quantity <= cart_data.discounts[discount_idx].extra_value.get("quantity", 1):
                        return {"sku": picked_sku, "quantity": Decimal(picked_quantity)}
            else:
                cart_data.discounts[discount_idx].status = ItemStatus.INVALID

        case ModifiersForDiscountRule.GRATIS_STEPPED:
            if picked_sku in skus_allowed_to_gratis:
                thresholds = get_thresholds_for_currency(discount.extra_value, currency_code)

                if not thresholds:
                    cart_data.discounts[discount_idx].status = ItemStatus.INVALID
                    return None

                closest_threshold, remaining_to_next_threshold, next_gratis_tier_quantity, _ = get_threshold_value(
                    thresholds, total_price
                )
                if total_price < Decimal(min(thresholds.keys(), key=lambda k: Decimal(k))):
                    cart_data.discounts[discount_idx].status = ItemStatus.INVALID

                    return None
                if closest_threshold and 0 < picked_quantity <= closest_threshold:
                    return {"sku": picked_sku, "quantity": Decimal(picked_quantity)}
            else:
                cart_data.discounts[discount_idx].status = ItemStatus.INVALID

        case _:
            return None
    return None


def filter_products_by_threshold_filters(discount_rule, cart_data, channel):
    """
    Filter products based on ThresholdProductFilter rules.
    Returns SKUs that should be counted towards gratis threshold.

    Args:
        discount_rule: DiscountRuleCode model instance
        cart_data: CartData with items
        channel: Channel instance

    Returns:
        List[str]: SKUs that match the threshold filters
    """
    if not hasattr(discount_rule, "threshold_filters") or not discount_rule.threshold_filters.exists():
        cart_items = cart_data.get_valid_items() if hasattr(cart_data, "get_valid_items") else []
        if not cart_items and hasattr(cart_data, "items"):
            cart_items = cart_data.items
        return [item.sku for item in cart_items]

    products_eligible = filter_by_inclusion_and_exclusion(
        discount_rule, cart_data, channel, filter_relation_name="threshold_filters"
    )
    return list(products_eligible.values_list("real_product__sku", flat=True))


def calculate_filtered_total_price(discount, cart_data, channel):
    """
    Calculate total price of cart considering only products that match ThresholdProductFilter.
    If no ThresholdProductFilter is defined, all products are counted.
    """
    filtered_skus = filter_products_by_threshold_filters(discount, cart_data, channel)

    cart_items = cart_data.get_valid_items() if hasattr(cart_data, "get_valid_items") else []
    if not cart_items and hasattr(cart_data, "items"):
        cart_items = cart_data.items

    items_with_prices = []
    for item in cart_items:
        if isinstance(item, GratisData):
            continue

        if item.sku in filtered_skus:
            price = (
                item.total_price
                if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
                else item.total_price_netto
            )
            items_with_prices.append((item.sku, price))

    total_price = sum(price for _, price in items_with_prices)
    return total_price


def is_gratis_available(discount, cart_data, total_price, channel, currency_code=None):
    gratis_is_allowed = False
    remaining_to_next_threshold = None
    min_price_next_tier = None

    if total_price is None:
        total_price = 9999999999
    else:
        if hasattr(discount, "threshold_filters") and discount.threshold_filters.exists():
            filtered_skus = filter_products_by_threshold_filters(discount, cart_data, channel)
            cart_items = cart_data.get_valid_items() if hasattr(cart_data, "get_valid_items") else []
            if not cart_items and hasattr(cart_data, "items"):
                cart_items = cart_data.items

            all_skus = [item.sku for item in cart_items]

            if not filtered_skus:
                total_price = 0
            elif set(filtered_skus) == set(all_skus):
                pass
            else:
                filtered_total = 0
                for item in cart_items:
                    if item.sku in filtered_skus:
                        if hasattr(item, "total_price") and item.total_price is not None:
                            filtered_total += item.total_price

                total_price = filtered_total

    if not discount:
        return False, 0, None, 0, None, {}

    if discount.modifier == ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART:
        gratis_is_allowed = check_is_gratis_allowed(cart_data, discount)
    match discount.modifier:
        case ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART:
            max_quantity = discount.extra_value.get("quantity", 1)
            sku_logic = discount.extra_value.get("sku_logic", "AND").upper()
            sku_count = 1 if sku_logic == "OR" else len(discount.extra_value.get("sku", []))
            required_skus = discount.extra_value.get("sku", [])
            cart_skus = [item.sku for item in cart_data.get_valid_items() if not getattr(item, "is_gratis", False)]
            if sku_logic == "OR":
                present_skus_count = 1 if any(sku in cart_skus for sku in required_skus) else 0
                missing_skus_count = 0 if present_skus_count else 1
            else:
                present_skus_count = sum(1 for sku in required_skus if sku in cart_skus)
                missing_skus_count = len(required_skus) - present_skus_count
            return (
                gratis_is_allowed,
                max_quantity,
                missing_skus_count,
                max_quantity,
                1 if sku_logic == "OR" else len(required_skus),
                {sku_count: max_quantity},
            )

        case ModifiersForDiscountRule.GRATIS_STEPPED:
            thresholds = get_thresholds_for_currency(discount.extra_value, currency_code)

            if not thresholds:
                return False, 0, None, 0, None, {}

            closest_threshold, remaining_to_next_threshold, next_gratis_tier_quantity, min_price_next_tier = (
                get_threshold_value(thresholds, total_price)
            )
            if closest_threshold:
                return (
                    True,
                    closest_threshold,
                    remaining_to_next_threshold,
                    next_gratis_tier_quantity,
                    min_price_next_tier,
                    thresholds,
                )
        case _:
            return None, 0, remaining_to_next_threshold, 0, min_price_next_tier, {}
    return False, 0, remaining_to_next_threshold, 0, min_price_next_tier, {}


def save_variable_when_lower_than_previous(x_prev, x_now):
    if x_prev and float(x_prev) > 0:
        if x_now:
            return x_now if float(x_now) < float(x_prev) else x_prev
        return x_prev
    return x_now


def has_threshold_products_in_cart(discount_rule, cart_data, channel):
    """
    Sprawdza czy koszyk zawiera jakiekolwiek produkty z ThresholdProductFilter.
    Zwraca True jeśli:
    - Nie ma zdefiniowanego ThresholdProductFilter (wszystkie produkty się liczą)
    - Są produkty w koszyku pasujące do ThresholdProductFilter
    """
    if not hasattr(discount_rule, "threshold_filters") or not discount_rule.threshold_filters.exists():
        return True

    filtered_skus = filter_products_by_threshold_filters(discount_rule, cart_data, channel)
    cart_items = cart_data.get_valid_items() if hasattr(cart_data, "get_valid_items") else []
    if not cart_items and hasattr(cart_data, "items"):
        cart_items = cart_data.items

    cart_skus = [item.sku for item in cart_items]
    return bool(set(filtered_skus) & set(cart_skus))


def get_all_available_gratis_rules(
    full_cart_body, total_price, channel, customer, email, country, currency, validated_discounts=None
):
    available_gratis_rules = []
    amount_missing_for_nearest_gratis_rule = None
    amount_required_for_nearest_gratis_rule = None
    cart_body = full_cart_body.cart
    all_gratis = (
        DiscountRuleCode.objects.filter(
            modifier__in=[ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART, ModifiersForDiscountRule.GRATIS_STEPPED],
            is_active=True,
        )
        .filter(Q(currencies__isnull=True) | Q(currencies__iso3=currency))
        .prefetch_related("codes", "threshold_filters")
    )
    code_already_applied = None
    if not code_already_applied:
        code_already_applied = [discount[0] for discount in validated_discounts] if validated_discounts else []

    for gratis in all_gratis:
        for code in gratis.codes.all():
            validated_discounts = None
            discount_data = DiscountData.factory()
            discount_data.code = code.code
            cart_body.discounts = [discount_data] + code_already_applied
            if not validated_discounts:
                validated_discounts = validate_discounts(
                    cart_data=cart_body,
                    total_price=total_price,
                    channel=channel,
                    customer=customer,
                    email=email,
                    currency=currency,
                )

            # NOTE: validate_discounts returns List[Tuple[ValidatedDiscountData, DiscountRuleCode]]
            # So validated_discount is the dataclass, c_discount_rule is the Django model
            for validated_discount, c_discount_rule in validated_discounts:
                (
                    gratis_is_allowed,
                    max_quantity,
                    amount_missing,
                    next_gratis_tier_quantity,
                    min_price_next_tier,
                    tier_data,
                ) = is_gratis_available(
                    discount=c_discount_rule,  # Pass the Django model, not dataclass
                    cart_data=cart_body,
                    total_price=total_price,
                    channel=channel,
                    currency_code=currency,
                )
                modifier = c_discount_rule.modifier if c_discount_rule else None
                if modifier in [ModifiersForDiscountRule.GRATIS_STEPPED]:
                    new_amount_required_for_nearest_gratis_rule = save_variable_when_lower_than_previous(
                        amount_required_for_nearest_gratis_rule, min_price_next_tier
                    )
                    if new_amount_required_for_nearest_gratis_rule is not None:
                        amount_required_for_nearest_gratis_rule = new_amount_required_for_nearest_gratis_rule
                    new_amount_missing_for_nearest_gratis_rule = save_variable_when_lower_than_previous(
                        amount_missing_for_nearest_gratis_rule, amount_missing
                    )
                    if new_amount_missing_for_nearest_gratis_rule is not None:
                        amount_missing_for_nearest_gratis_rule = new_amount_missing_for_nearest_gratis_rule

                # Skip gratis if there are no available items and no next tier to reach
                # This happens when currency is not supported (tier_data is empty for GRATIS_STEPPED)
                has_available_gratis = not (not max_quantity and not next_gratis_tier_quantity and not amount_missing)

                if gratis.modifier == ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART:
                    should_show_gratis = has_available_gratis and gratis_is_allowed
                else:
                    should_show_gratis = has_available_gratis and (
                        gratis_is_allowed
                        or (SHOW_INVALID_GRATIS and has_threshold_products_in_cart(gratis, cart_body, channel))
                    )

                if should_show_gratis:
                    products_egible_to_gratis = filter_by_inclusion_and_exclusion(gratis, cart_body, channel)
                    skus_egible_to_gratis = filter_saleable_skus(
                        products_egible_to_gratis.values_list("real_product__sku", flat=True), channel, customer
                    )
                    if GRATIS_MECHANISM == 2:
                        sku_list_with_price = dict.fromkeys(skus_egible_to_gratis, GRATIS_PRICE)
                    else:
                        sku_list_with_price = calc_base_unit_price_for_gratis(
                            skus_egible_to_gratis, channel, customer, country, currency, full_cart_body.addresses
                        )

                    if sku_list_with_price:
                        available_gratis_rules.append(
                            asdict(
                                GratisRules(
                                    items=[
                                        {"sku": sku, "price": base_price}
                                        for sku, base_price in sku_list_with_price.items()
                                    ],
                                    code=code.code,
                                    name=gratis.name,
                                    next_gratis_tier_quantity=next_gratis_tier_quantity,
                                    price_missing_to_next_gratis_tier=(
                                        amount_missing
                                        if c_discount_rule.modifier == ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART
                                        else (
                                            round(Decimal(amount_missing_for_nearest_gratis_rule), 2)
                                            if amount_missing_for_nearest_gratis_rule
                                            else 0
                                        )
                                    ),
                                    next_gratis_tier_price=(
                                        min_price_next_tier
                                        if c_discount_rule.modifier == ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART
                                        else (round(Decimal(min_price_next_tier), 2) if min_price_next_tier else None)
                                    ),
                                    all_tiers=tier_data,
                                    max_available_quantity=(
                                        max_quantity if (gratis_is_allowed or gratis.show_when_invalid) else 0
                                    ),
                                    is_available=gratis_is_allowed,
                                    extension=gratis.extension if hasattr(gratis, "extension") else None,
                                )
                            )
                        )
                    break
    return available_gratis_rules, amount_required_for_nearest_gratis_rule, amount_missing_for_nearest_gratis_rule


def format_available_gratis(available_gratis_rules: list) -> list | None:
    if not available_gratis_rules:
        return None
    return available_gratis_rules
