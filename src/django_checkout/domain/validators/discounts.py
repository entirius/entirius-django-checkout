# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db.models import BooleanField, Exists, F, OuterRef, Q, Value
from django.forms.models import model_to_dict
from django.utils import timezone

from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.dto.discount import ValidatedDiscountData
from django_checkout.domain.dto.discount_items import DiscountItems
from django_checkout.enums import ItemStatus
from django_checkout.models import DiscountCode, DiscountRuleCode, ModifiersForDiscountRule, Order
from django_checkout.models.discount_customer_mode_of_actions import DiscountModeOfActionType
from django_checkout.models.discount_rule_code import TargetForDiscountRule

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


def get_highest_quantity(cart_data: CartData | DiscountItems):
    if not cart_data.items:
        return 0

    all_quantity = [item.quantity for item in cart_data.get_valid_items() if item.quantity]
    if all_quantity:
        return max(all_quantity)
    else:
        return 0


def customer_modifier_filter(discount_rule_query, customer):
    main_discount_rule_filters = []
    discount_rule_query = discount_rule_query.distinct()
    for discount_rule in discount_rule_query:
        # należy mode_of_action te o części wspólnej złączyć AND a te bez części wspólnej OR
        for take_common_part in [True, False]:
            filters_discount_rule = []
            discount_rule_customer_modes_of_action = discount_rule.discount_customer_mode_of_actions_rule.filter(
                take_common_part=take_common_part
            ).order_by("is_inclusion_or_exclusion")
            # dla każdego mode_of_action o danym take_common_part procesujemy
            for customer_mode_of_action in discount_rule_customer_modes_of_action:
                filters_customer_mode_of_action = []
                moa_group_exists = customer_mode_of_action.groups.exists()
                moa_customer_exists = customer_mode_of_action.customers.exists()

                # jeżeli są zadeklarowani klienci lub grupy w mode_of_action a w koszyku nie ma customera to wykluczamy discount_rule
                if (moa_group_exists or moa_customer_exists) and not customer:
                    filters_discount_rule.append(~Q(pk=discount_rule.pk))
                    continue

                negate_filter = (
                    True
                    if customer_mode_of_action.is_inclusion_or_exclusion == DiscountModeOfActionType.EXCLUSION
                    else False
                )

                # Jeżeli w mode_of_action jest zadeklarowany customer to sprawdzamy, czy ten z koszyka jest wśród nich,
                # jeżeli jest wśród nich to dodajemy do filtra, jeżeli nie to dodajemy negację
                if moa_customer_exists:
                    if customer_mode_of_action.customers.filter(pk=customer.pk).exists():
                        filters_customer_mode_of_action.append(Q(pk=discount_rule.pk, _negated=negate_filter))
                    else:
                        filters_customer_mode_of_action.append(~Q(pk=discount_rule.pk, _negated=negate_filter))

                # jeżeli w mode_of_action jest zadeklarowana groupa to sprawdzamy groupa customera z koszyka jest wśród nich
                # jeżeli jest wśród nich to dodajemy do filtra, jeżeli nie to dodajemy negację
                if moa_group_exists:
                    if customer.group is None:
                        filters_customer_mode_of_action.append(~Q(pk=discount_rule.pk, _negated=negate_filter))
                    else:
                        if customer_mode_of_action.groups.filter(pk=customer.group.pk).exists():
                            filters_customer_mode_of_action.append(Q(pk=discount_rule.pk, _negated=negate_filter))
                        else:
                            filters_customer_mode_of_action.append(~Q(pk=discount_rule.pk, _negated=negate_filter))

                (
                    filters_discount_rule.append(Q(*filters_customer_mode_of_action, _connector=Q.AND))
                    if filters_customer_mode_of_action
                    else None
                )

            if take_common_part:
                (
                    (main_discount_rule_filters.append(Q(*filters_discount_rule, _connector=Q.AND)))
                    if filters_discount_rule
                    else None
                )
            else:
                (
                    (main_discount_rule_filters.append(Q(*filters_discount_rule, _connector=Q.OR)))
                    if filters_discount_rule
                    else None
                )
    main_discount_rule_filters = Q(*main_discount_rule_filters, _connector=Q.OR) if main_discount_rule_filters else Q()
    return discount_rule_query.filter(main_discount_rule_filters)


def is_currency_supported_by_discount(extra_value, currency_code):
    """
    Sprawdza czy waluta jest obsługiwana przez regułę rabatową.
    Zwraca True jeśli:
    - extra_value jest prostą wartością (int/float) - obsługuje wszystkie waluty
    - extra_value jest dict ale nie ma kluczy walutowych - obsługuje wszystkie waluty
    - extra_value jest dict z kluczami walutowymi i zawiera currency_code
    Zwraca False jeśli:
    - extra_value jest dict z kluczami walutowymi ale nie zawiera currency_code
    """
    if not currency_code:
        return True

    if isinstance(extra_value, (int, float)):
        return True

    if isinstance(extra_value, list):
        return True

    if isinstance(extra_value, dict):
        if not extra_value:
            return True

        first_key = next(iter(extra_value.keys()))
        first_value = extra_value[first_key]

        is_currency_format = (
            isinstance(first_key, str) and len(first_key) == 3 and first_key.isupper() and first_key.isalpha()
        ) or isinstance(first_value, dict)

        if is_currency_format:
            return currency_code in extra_value
        else:
            return True

    return True


def validate_discounts(
    cart_data: CartData | DiscountItems,
    total_price,
    channel: "Channel" = None,
    customer=None,
    email=None,
    allow_for_discount_codes=True,
    currency=None,
) -> tuple[list[ValidatedDiscountData], DiscountRuleCode | None]:
    # I cant change list of codes to str code (because it won't work for the previous version of checkout), so I just take first one

    discounts = cart_data.discounts if hasattr(cart_data, "discounts") else []
    if not allow_for_discount_codes:
        return [
            ValidatedDiscountData(
                code=discount.code,
                item_code=None,
                free_shipping=False,
                free_shippping_methods=[],
                price_discount=0,
                percent_discount=0,
                status=ItemStatus.INVALID,
                free_order=False,
                min_order_amount=0,
                extra_value=0,
                target=None,
                modifier=None,
                sku=None,
                quantity=0,
                clear_discounts=discount.clear_discounts,
                rejection_reason="blocked_by_price_rules",
                rejection_meta={"reason": "price_tuner"},
            )
            for discount in discounts
        ], None

    current_date = timezone.now().date()
    is_active_from = Q(codes__active_from__lte=current_date) | Q(codes__active_from__isnull=True)
    is_active_to = Q(codes__active_to__gte=current_date) | Q(codes__active_to__isnull=True)
    is_turn_on = Q(is_active=True)
    is_active = is_active_from & is_active_to & is_turn_on
    code_is_active_from = Q(active_from__lte=current_date) | Q(active_from__isnull=True)
    code_is_active_to = Q(active_to__gte=current_date) | Q(active_to__isnull=True)
    highest_quantity = get_highest_quantity(cart_data)
    highest_quantity_filter = Q(codes__max_products_qty__isnull=True) | Q(codes__max_products_qty__gt=highest_quantity)
    discount_validated = []
    is_logged = False
    if customer:
        email = customer.user.email
        is_logged = True

    have_previous_order_by_email = Order.objects.have_previous_order_by_email(channel, email)

    base_query = DiscountRuleCode.objects.select_related().filter(min_order_amount__lte=total_price)

    if channel:
        base_query = base_query.filter(channels=channel)

    if discounts:
        discount_codes = [discount.code for discount in discounts]
        base_query = base_query.filter(Q(codes__code__in=discount_codes) & is_active_from & is_active_to)

    base_query = (
        base_query.annotate(
            have_previous_order_by_email=Value(have_previous_order_by_email, output_field=BooleanField()),
            is_logged=Value(is_logged, output_field=BooleanField()),
        )
        .filter(
            Q(target=TargetForDiscountRule.ALL)
            | Q(Q(target=TargetForDiscountRule.FIRST_ORDER_ALL) & Q(have_previous_order_by_email=False))
            | Q(
                Q(target=TargetForDiscountRule.FIRST_ORDER_LOGGED)
                & Q(have_previous_order_by_email=False)
                & Q(is_logged=True)
            )
        )
        .filter(is_active, highest_quantity_filter)
        .distinct()
        .order_by("priority", "automatic_applications", "created_at")
    )  # jeśli prioryty nie jest ustawione decyduje data dotania reguły

    base_query = customer_modifier_filter(base_query, customer)

    if discounts:
        query = base_query  # Already filtered for discount_codes in base_query above
    else:
        query = base_query.none()

    # Query dla reguł automatycznych (NIE filtrowany po kodach użytkownika)
    # Dzięki temu automatyczne reguły jak Kaskada będą zawsze uwzględniane
    query_automatic = base_query.filter(automatic_applications=True).exclude(
        modifier__in=[ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART, ModifiersForDiscountRule.GRATIS_STEPPED]
    )

    if discounts:
        combined_query = base_query.filter(Q(codes__code__in=discount_codes) | Q(automatic_applications=True))
    else:
        combined_query = base_query.filter(automatic_applications=True)

    highest_priority_discount_rule = combined_query.first()

    if discounts:
        valid_used_codes = DiscountCode.objects.filter(
            rule=OuterRef("pk"),
            code__in=discount_codes,
            current_used__lt=F("max_used"),
        )
        query = query.filter(
            Exists(valid_used_codes),
            automatic_applications=False,
        )

    def factor_invalid_discount(rejection_reason=None, rejection_meta=None):
        return (
            ValidatedDiscountData(
                code=discount.code,
                item_code=None,
                free_shipping=False,
                free_shipping_methods=[],
                price_discount=0,
                percent_discount=0,
                status=ItemStatus.INVALID,
                free_order=False,
                min_order_amount=0,
                extra_value=0,
                target=None,
                modifier=None,
                sku=None,
                quantity=0,
                is_automatic=False,
                clear_discounts=discount.clear_discounts,
                rejection_reason=rejection_reason,
                rejection_meta=rejection_meta,
            ),
            None,
        )

    if highest_priority_discount_rule:
        if not highest_priority_discount_rule.combine_with_other_rules:
            query = query.filter(pk=highest_priority_discount_rule.pk)
            query_automatic = query_automatic.filter(pk=highest_priority_discount_rule.pk)
        else:
            query = query.filter(combine_with_other_rules=True)
            query_automatic = query_automatic.filter(combine_with_other_rules=True)

    how_many_orders_user_with_discount_code_exists = Order.objects.have_previous_order_with_discount_code(
        channel, email
    )
    code_filters = []
    for code, count in how_many_orders_user_with_discount_code_exists.items():
        code_filters.append(~Q(Q(code=code) & Q(max_uses_per_user__lte=count) & Q(max_uses_per_user__isnull=False)))

    rules_calc_pk = []

    if discounts:
        all_discount_codes = list(
            DiscountCode.objects.select_related("rule")
            .filter(rule__in=query, *code_filters)
            .filter(code_is_active_from & code_is_active_to)
        )

        valid_codes = set(dc.code for dc in all_discount_codes)

        rules_by_code = {dc.code: dc.rule for dc in all_discount_codes if dc.code in valid_codes}

        for discount in discounts:
            if getattr(discount, "is_automatic", False):
                continue
            discount_codes = DiscountCode.objects.select_related("rule").filter(
                rule__in=query, *code_filters, current_used__lt=F("max_used")
            )

            if discount.code in valid_codes and not getattr(discount, "is_automatic", False):
                dc = rules_by_code.get(discount.code)

                if dc.pk in rules_calc_pk:
                    factor_invalid_discount(
                        rejection_reason="duplicate_rule",
                        rejection_meta={"code": discount.code},
                    )
                    continue

                rules_calc_pk.append(dc.pk)

                if not is_currency_supported_by_discount(dc.extra_value, currency):
                    discount_validated.append(
                        factor_invalid_discount(
                            rejection_reason="currency_not_supported",
                            rejection_meta={"code": discount.code, "currency": currency},
                        )
                    )
                    continue

                rule_dict = model_to_dict(
                    dc, fields=["free_order", "extra_value", "min_order_amount", "target", "modifier"]
                )
                rule_dict["code"] = discount.code
                rule_dict["min_order_amount"] = float(rule_dict["min_order_amount"])
                # will be filled while calculating discount
                rule_dict["free_shipping"] = dc.free_shipping
                rule_dict["free_shipping_methods"] = []
                rule_dict["price_discount"] = 0
                rule_dict["percent_discount"] = 0
                rule_dict["item_code"] = None
                rule_dict["sku"] = getattr(discount, "sku", None)
                rule_dict["quantity"] = getattr(discount, "quantity", None)
                rule_dict["is_automatic"] = False
                rule_dict["clear_discounts"] = getattr(discount, "clear_discounts", False)
                discount_validated.append((ValidatedDiscountData(**rule_dict, status=ItemStatus.VALID), dc))
            else:
                discount_validated.append(
                    factor_invalid_discount(
                        rejection_reason="code_not_found",
                        rejection_meta={"code": discount.code},
                    )
                )

    for qa in query_automatic:
        if not is_currency_supported_by_discount(qa.extra_value, currency):
            continue

        rule_dict = model_to_dict(qa, fields=["free_order", "extra_value", "min_order_amount", "target", "modifier"])
        # Use prefetched codes to avoid N+1 query
        codes_list = list(qa.codes.all())
        rule_dict["code"] = codes_list[0].code if codes_list else qa.name

        # will be filled while calculating discount
        rule_dict["free_shipping"] = False
        rule_dict["free_shipping_methods"] = []
        rule_dict["price_discount"] = 0
        rule_dict["percent_discount"] = 0
        rule_dict["item_code"] = None
        rule_dict["sku"] = None
        rule_dict["quantity"] = None
        rule_dict["is_automatic"] = True
        rule_dict["clear_discounts"] = False
        discount_validated.append((ValidatedDiscountData(**rule_dict, status=ItemStatus.VALID), qa))

    return discount_validated
