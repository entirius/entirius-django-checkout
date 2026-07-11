# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Discount admin service — all ORM + business logic for the v2 admin discount API.

Channel-scoped: every rule is filtered/validated against the URL channel via the
DiscountRuleCode.channels M2M, so an admin acting on channel A cannot read or mutate a
rule that belongs only to channel B. Views stay thin and delegate here.

Raises:
    ObjectDoesNotExist — resource missing or outside the channel (view -> 404).
    ValueError — invalid modifier/target/inclusion value (view -> 400).
"""

import logging
import uuid as uuid_mod

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Q, QuerySet
from django_accounts.models import Customer, Group
from django_pim.models import Attribute, Feature, Product, ProductCategory
from django_regional.models import Currency

from django_checkout.models import (
    Channel,
    DiscountCode,
    DiscountCustomerModeOfAction,
    DiscountModeOfAction,
    DiscountRuleCode,
    FilterModeType,
    ModifiersForDiscountRule,
    ShippingMethod,
    TargetForDiscountRule,
    ThresholdProductFilter,
)
from django_checkout.schemas.common import format_money
from django_checkout.schemas.requests.discount import (
    CustomerFilterInput,
    DiscountCodeInput,
    DiscountCodeUpdateInput,
    DiscountRuleCreateInput,
    DiscountRuleUpdateInput,
    ProductFilterInput,
)

logger = logging.getLogger("checkout.discount_service")

_VALID_MODIFIERS = set(ModifiersForDiscountRule.values)
_VALID_TARGETS = set(TargetForDiscountRule.values)
_VALID_INCLUSION = set(FilterModeType.values)
_BOOL_MAP = {"true": True, "false": False, "1": True, "0": False}

# Per-modifier hint so the CMS renders the right extra_value sub-form (mirrors worker/discount_calc.py).
_EXTRA_VALUE_KIND = {
    ModifiersForDiscountRule.NONE: "none",
    ModifiersForDiscountRule.PERCENT_DISCOUNT: "percent",
    ModifiersForDiscountRule.PRICE_DISCOUNT: "amount",
    ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT: "threshold_percent",
    ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART: "threshold_percent",
    ModifiersForDiscountRule.STEP_PRICE_PERCENT_DISCOUNT: "threshold_percent",
    ModifiersForDiscountRule.STEP_QTY_PRICE_DISCOUNT_WHOLE_CART: "threshold_amount",
    ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY: "currency_threshold_price",
    ModifiersForDiscountRule.CHEAPEST_GRATIS: "gratis_qty",
    ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS: "gratis_qty",
    ModifiersForDiscountRule.GRATIS_STEPPED: "gratis_threshold",
    ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART: "gratis_sku",
}

_RULE_SCALARS = (
    "name",
    "modifier",
    "extra_value",
    "target",
    "min_order_amount",
    "free_shipping",
    "free_order",
    "is_omnibus",
    "is_active",
    "show_when_invalid",
    "combine_with_other_rules",
    "automatic_applications",
    "priority",
    "extension",
)
_FILTER_SCALARS = (
    "take_common_part",
    "is_inclusion_or_exclusion",
    "product_price_from",
    "product_price_to",
    "cart_price_from",
    "cart_price_to",
    "qty_from",
    "qty_to",
    "cart_qty_from",
    "cart_qty_to",
)
# kind -> (concrete model, rule related_name)
_FILTER_MODELS = {
    "product": (DiscountModeOfAction, "discount_mode_of_actions_rule"),
    "threshold": (ThresholdProductFilter, "threshold_filters"),
}
_RULE_PREFETCH = (
    "channels",
    "currencies",
    "free_shipping_methods",
    "discount_mode_of_actions_rule",
    "threshold_filters",
    "discount_customer_mode_of_actions_rule",
)
# Codes are NOT prefetched on the rule (a rule can have thousands) — fetched via the paginated codes endpoint.
_CODE_ORDERING = {
    "code",
    "-code",
    "current_used",
    "-current_used",
    "max_used",
    "-max_used",
    "max_uses_per_user",
    "-max_uses_per_user",
    "max_products_qty",
    "-max_products_qty",
    "active_from",
    "-active_from",
    "active_to",
    "-active_to",
    "id",
    "-id",
}


# --- shared helpers ---


def parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return _BOOL_MAP.get(str(value).lower())


def _validate_enums(*, modifier: str | None = None, target: str | None = None, inclusion: str | None = None) -> None:
    if modifier is not None and modifier not in _VALID_MODIFIERS:
        raise ValueError(f"Invalid modifier '{modifier}'. Allowed: {sorted(_VALID_MODIFIERS)}")
    if target is not None and target not in _VALID_TARGETS:
        raise ValueError(f"Invalid target '{target}'. Allowed: {sorted(_VALID_TARGETS)}")
    if inclusion is not None and inclusion not in _VALID_INCLUSION:
        raise ValueError(f"Invalid is_inclusion_or_exclusion '{inclusion}'. Allowed: {sorted(_VALID_INCLUSION)}")


def _apply_scalars(obj, data, field_names: tuple[str, ...], *, partial: bool) -> None:
    payload = data.model_dump(include=set(field_names), exclude_unset=partial)
    for field, value in payload.items():
        setattr(obj, field, value)


def _valid_uuids(values: list[str]) -> list[str]:
    """Drop malformed UUIDs so a bad input yields no match instead of a 500."""
    result = []
    for value in values:
        try:
            result.append(str(uuid_mod.UUID(str(value))))
        except (ValueError, AttributeError, TypeError):
            continue
    return result


def _get_rule(channel: Channel, rule_id: int) -> DiscountRuleCode:
    try:
        return DiscountRuleCode.objects.prefetch_related(*_RULE_PREFETCH).get(pk=rule_id, channels=channel)
    except DiscountRuleCode.DoesNotExist as exc:
        raise ObjectDoesNotExist(f"Discount rule {rule_id} not found in channel {channel.idx}") from exc


# --- rule CRUD ---


def list_rules(channel: Channel, params) -> QuerySet:
    qs = (
        DiscountRuleCode.objects.filter(channels=channel)
        .annotate(code_count_anno=Count("codes", distinct=True))
        .prefetch_related("channels")
        .distinct()
    )
    if (is_active := parse_bool(params.get("is_active"))) is not None:
        qs = qs.filter(is_active=is_active)
    if (automatic := parse_bool(params.get("automatic"))) is not None:
        qs = qs.filter(automatic_applications=automatic)
    if modifier := params.get("modifier"):
        qs = qs.filter(modifier=modifier)
    if target := params.get("target"):
        qs = qs.filter(target=target)
    if search := params.get("search"):
        qs = qs.filter(Q(name__icontains=search) | Q(codes__code__icontains=search)).distinct()
    return _order_rules(qs, params.get("ordering"))


def _order_rules(qs: QuerySet, ordering: str | None) -> QuerySet:
    allowed = {"created_at", "-created_at", "priority", "-priority", "name", "-name"}
    return qs.order_by(ordering if ordering in allowed else "-created_at")


@transaction.atomic
def create_rule(channel: Channel, data: DiscountRuleCreateInput) -> DiscountRuleCode:
    _validate_enums(modifier=data.modifier, target=data.target)
    rule = DiscountRuleCode()
    _apply_scalars(rule, data, _RULE_SCALARS, partial=False)
    rule.save()
    _set_rule_m2m(rule, channel, channels=data.channels, currencies=data.currencies, methods=data.free_shipping_methods)
    for code_input in data.codes:
        _create_code(rule, code_input)
    return _get_rule(channel, rule.pk)


@transaction.atomic
def update_rule(channel: Channel, rule_id: int, data: DiscountRuleUpdateInput) -> DiscountRuleCode:
    _validate_enums(modifier=data.modifier, target=data.target)
    rule = _get_rule(channel, rule_id)
    _apply_scalars(rule, data, _RULE_SCALARS, partial=True)
    rule.save()
    provided = data.model_fields_set
    if "channels" in provided:
        _set_rule_m2m(rule, channel, channels=data.channels or [], currencies=None, methods=None)
    if "currencies" in provided:
        rule.currencies.set(Currency.objects.filter(iso3__in=[c.upper() for c in (data.currencies or [])]))
    if "free_shipping_methods" in provided:
        rule.free_shipping_methods.set(_methods_qs(channel, data.free_shipping_methods or []))
    return _get_rule(channel, rule.pk)


def get_discount_meta() -> dict:
    """Enum choices + per-modifier extra_value hint for data-driven CMS forms."""
    return {
        "modifiers": [
            {"value": value, "label": str(label), "extra_value_kind": _EXTRA_VALUE_KIND.get(value, "json")}
            for value, label in ModifiersForDiscountRule.choices
        ],
        "targets": [{"value": value, "label": str(label)} for value, label in TargetForDiscountRule.choices],
        "inclusion_modes": [{"value": value, "label": str(label)} for value, label in FilterModeType.choices],
    }


def _t9n_name(name_t9n) -> str | None:
    """Best-effort display name from a translation dict (prefer English, else first)."""
    if not isinstance(name_t9n, dict) or not name_t9n:
        return None
    return name_t9n.get("en") or next(iter(name_t9n.values()), None)


def list_shipping_methods(channel: Channel) -> list[dict]:
    """Shipping methods for the channel — options for the rule's free_shipping_methods multi-select."""
    methods = ShippingMethod.objects.filter(channel=channel).order_by("position", "code")
    return [{"code": m.code, "channel_idx": channel.idx, "name": _t9n_name(m.name_t9n)} for m in methods]


def list_currencies(channel: Channel) -> list[dict]:
    """Currencies for the channel — options for the rule's currencies multi-select and per-currency thresholds.

    Falls back to all currencies if the channel has none configured.
    """
    currencies = channel.currencies.all().order_by("iso3")
    if not currencies.exists():
        currencies = Currency.objects.all().order_by("iso3")
    return [{"iso3": c.iso3, "name": c.name_en, "symbol": c.symbol} for c in currencies]


def list_channels() -> list[dict]:
    """All checkout channels — options for the rule's channels multi-select.

    Not channel-scoped: a rule may target any subset of channels (the URL
    channel is auto-added on create). Mirrors `checkout.Channel` (idx + label).
    """
    return [{"idx": c.idx, "name": c.label} for c in Channel.objects.all().order_by("idx")]


def get_rule(channel: Channel, rule_id: int) -> DiscountRuleCode:
    """Public fetch of a single rule scoped to the channel (view -> 404 on miss)."""
    return _get_rule(channel, rule_id)


def delete_rule(channel: Channel, rule_id: int) -> None:
    _get_rule(channel, rule_id).delete()


_BULK_ACTIONS = frozenset({"activate", "deactivate", "delete"})


def bulk_rules(channel: Channel, *, action: str, ids=None, all_matching: bool = False, params=None) -> int:
    """Bulk activate/deactivate/delete rules — by `ids`, or ALL rules matching the list `params`.

    Channel-scoped: only rules belonging to this channel are touched. Returns the affected count.
    """
    if action not in _BULK_ACTIONS:
        raise ValueError(f"Invalid action '{action}'. Allowed: {sorted(_BULK_ACTIONS)}")
    if all_matching:
        base = list_rules(channel, params or {})
    elif ids:
        base = DiscountRuleCode.objects.filter(channels=channel, pk__in=ids)
    else:
        return 0
    pks = list(base.values_list("pk", flat=True).distinct())
    if not pks:
        return 0
    target = DiscountRuleCode.objects.filter(pk__in=pks)
    if action == "delete":
        target.delete()
    else:
        target.update(is_active=(action == "activate"))
    return len(pks)


def _set_rule_m2m(rule, channel, *, channels, currencies, methods) -> None:
    """Set rule M2M. The URL channel is always part of `channels`. None args are skipped."""
    if channels is not None:
        rule.channels.set(Channel.objects.filter(idx__in=set(channels) | {channel.idx}))
    if currencies is not None:
        rule.currencies.set(Currency.objects.filter(iso3__in=[c.upper() for c in currencies]))
    if methods is not None:
        rule.free_shipping_methods.set(_methods_qs(channel, methods))


def _methods_qs(channel: Channel, codes: list[str]) -> QuerySet:
    return ShippingMethod.objects.filter(code__in=codes, channel=channel)


# --- code CRUD ---


def list_codes(channel: Channel, rule_id: int, params=None) -> QuerySet:
    """Codes for a rule, filtered/sorted — paginated by the view (a rule can have thousands)."""
    qs = _get_rule(channel, rule_id).codes.all()
    params = params or {}
    if search := params.get("search"):
        qs = qs.filter(code__icontains=search)
    ordering = params.get("ordering")
    return qs.order_by(ordering if ordering in _CODE_ORDERING else "-id")


def create_code(channel: Channel, rule_id: int, data: DiscountCodeInput) -> DiscountCode:
    return _create_code(_get_rule(channel, rule_id), data)


def _create_code(rule: DiscountRuleCode, data: DiscountCodeInput) -> DiscountCode:
    code = DiscountCode(
        rule=rule,
        code=data.code,
        max_used=data.max_used,
        max_uses_per_user=data.max_uses_per_user,
        active_from=data.active_from,
        active_to=data.active_to,
        max_products_qty=data.max_products_qty,
    )
    code.save()
    return code


def update_code(channel: Channel, rule_id: int, code_id: int, data: DiscountCodeUpdateInput) -> DiscountCode:
    code = _get_code(_get_rule(channel, rule_id), code_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(code, field, value)
    code.save()
    return code


def delete_code(channel: Channel, rule_id: int, code_id: int) -> None:
    _get_code(_get_rule(channel, rule_id), code_id).delete()


def _get_code(rule: DiscountRuleCode, code_id: int) -> DiscountCode:
    try:
        return rule.codes.get(pk=code_id)
    except DiscountCode.DoesNotExist as exc:
        raise ObjectDoesNotExist(f"Discount code {code_id} not found in rule {rule.pk}") from exc


# --- product / threshold filter CRUD (shared shape) ---


def list_product_filters(channel: Channel, rule_id: int, kind: str) -> list:
    _, relation = _FILTER_MODELS[kind]
    return list(getattr(_get_rule(channel, rule_id), relation).all())


@transaction.atomic
def create_product_filter(channel: Channel, rule_id: int, data: ProductFilterInput, kind: str):
    _validate_enums(inclusion=data.is_inclusion_or_exclusion)
    model, _ = _FILTER_MODELS[kind]
    filter_obj = model(rule=_get_rule(channel, rule_id))
    _apply_scalars(filter_obj, data, _FILTER_SCALARS, partial=False)
    filter_obj.save()
    _set_filter_m2m(filter_obj, channel, data, partial=False)
    return filter_obj


@transaction.atomic
def update_product_filter(channel: Channel, rule_id: int, filter_id: int, data: ProductFilterInput, kind: str):
    if "is_inclusion_or_exclusion" in data.model_fields_set:
        _validate_enums(inclusion=data.is_inclusion_or_exclusion)
    filter_obj = _get_filter(_get_rule(channel, rule_id), filter_id, kind)
    _apply_scalars(filter_obj, data, _FILTER_SCALARS, partial=True)
    filter_obj.save()
    _set_filter_m2m(filter_obj, channel, data, partial=True)
    return filter_obj


def delete_product_filter(channel: Channel, rule_id: int, filter_id: int, kind: str) -> None:
    _get_filter(_get_rule(channel, rule_id), filter_id, kind).delete()


def _get_filter(rule: DiscountRuleCode, filter_id: int, kind: str):
    model, relation = _FILTER_MODELS[kind]
    try:
        return getattr(rule, relation).get(pk=filter_id)
    except model.DoesNotExist as exc:
        raise ObjectDoesNotExist(f"{kind} filter {filter_id} not found in rule {rule.pk}") from exc


def _set_filter_m2m(filter_obj, channel: Channel, data: ProductFilterInput, *, partial: bool) -> None:
    provided = data.model_fields_set
    resolvers = {
        "attributes": lambda v: Attribute.objects.filter(idx__in=v),
        "features_qty_greater_than_attr_value": lambda v: Feature.objects.filter(idx__in=v),
        "features_qty_is_multiple_of_attr_value": lambda v: Feature.objects.filter(idx__in=v),
        "categories": lambda v: ProductCategory.objects.filter(idx__in=v, shop__idx=channel.idx),
        "products": lambda v: Product.objects.filter(real_product__sku__in=v, shop__idx=channel.idx),
    }
    for field, resolver in resolvers.items():
        if partial and field not in provided:
            continue
        getattr(filter_obj, field).set(resolver(getattr(data, field)))


# --- customer filter CRUD ---


def list_customer_filters(channel: Channel, rule_id: int) -> list[DiscountCustomerModeOfAction]:
    return list(_get_rule(channel, rule_id).discount_customer_mode_of_actions_rule.all())


@transaction.atomic
def create_customer_filter(channel: Channel, rule_id: int, data: CustomerFilterInput) -> DiscountCustomerModeOfAction:
    _validate_enums(inclusion=data.is_inclusion_or_exclusion)
    filter_obj = DiscountCustomerModeOfAction(
        rule=_get_rule(channel, rule_id),
        take_common_part=data.take_common_part,
        is_inclusion_or_exclusion=data.is_inclusion_or_exclusion,
    )
    filter_obj.save()
    _set_customer_m2m(filter_obj, data, partial=False)
    return filter_obj


@transaction.atomic
def update_customer_filter(
    channel: Channel, rule_id: int, filter_id: int, data: CustomerFilterInput
) -> DiscountCustomerModeOfAction:
    if "is_inclusion_or_exclusion" in data.model_fields_set:
        _validate_enums(inclusion=data.is_inclusion_or_exclusion)
    filter_obj = _get_customer_filter(_get_rule(channel, rule_id), filter_id)
    payload = data.model_dump(include={"take_common_part", "is_inclusion_or_exclusion"}, exclude_unset=True)
    for field, value in payload.items():
        setattr(filter_obj, field, value)
    filter_obj.save()
    _set_customer_m2m(filter_obj, data, partial=True)
    return filter_obj


def delete_customer_filter(channel: Channel, rule_id: int, filter_id: int) -> None:
    _get_customer_filter(_get_rule(channel, rule_id), filter_id).delete()


def _get_customer_filter(rule: DiscountRuleCode, filter_id: int) -> DiscountCustomerModeOfAction:
    try:
        return rule.discount_customer_mode_of_actions_rule.get(pk=filter_id)
    except DiscountCustomerModeOfAction.DoesNotExist as exc:
        raise ObjectDoesNotExist(f"Customer filter {filter_id} not found in rule {rule.pk}") from exc


def _set_customer_m2m(filter_obj, data: CustomerFilterInput, *, partial: bool) -> None:
    provided = data.model_fields_set
    if not partial or "customers" in provided:
        filter_obj.customers.set(Customer.objects.filter(uid__in=_valid_uuids(data.customers)))
    if not partial or "groups" in provided:
        filter_obj.groups.set(Group.objects.filter(code__in=data.groups))


# --- serializers (domain -> dict, shaped to schemas/responses/discount.py) ---


def serialize_code(code: DiscountCode) -> dict:
    return {
        "id": code.pk,
        "code": code.code,
        "max_used": code.max_used,
        "max_uses_per_user": code.max_uses_per_user,
        "current_used": code.current_used,
        "active_from": code.active_from,
        "active_to": code.active_to,
        "max_products_qty": code.max_products_qty,
    }


def serialize_product_filter(filter_obj) -> dict:
    return {
        "id": filter_obj.pk,
        "is_inclusion_or_exclusion": filter_obj.is_inclusion_or_exclusion,
        "take_common_part": filter_obj.take_common_part,
        "attributes": list(filter_obj.attributes.values_list("idx", flat=True)),
        "features_qty_greater_than_attr_value": list(
            filter_obj.features_qty_greater_than_attr_value.values_list("idx", flat=True)
        ),
        "features_qty_is_multiple_of_attr_value": list(
            filter_obj.features_qty_is_multiple_of_attr_value.values_list("idx", flat=True)
        ),
        "categories": list(filter_obj.categories.values_list("idx", flat=True)),
        "products": list(filter_obj.products.values_list("real_product__sku", flat=True)),
        "product_price_from": filter_obj.product_price_from,
        "product_price_to": filter_obj.product_price_to,
        "cart_price_from": filter_obj.cart_price_from,
        "cart_price_to": filter_obj.cart_price_to,
        "qty_from": filter_obj.qty_from,
        "qty_to": filter_obj.qty_to,
        "cart_qty_from": filter_obj.cart_qty_from,
        "cart_qty_to": filter_obj.cart_qty_to,
    }


def serialize_customer_filter(filter_obj: DiscountCustomerModeOfAction) -> dict:
    return {
        "id": filter_obj.pk,
        "is_inclusion_or_exclusion": filter_obj.is_inclusion_or_exclusion,
        "take_common_part": filter_obj.take_common_part,
        "customers": [str(u) for u in filter_obj.customers.values_list("uid", flat=True)],
        "groups": list(filter_obj.groups.values_list("code", flat=True)),
    }


def serialize_rule_list_item(rule: DiscountRuleCode) -> dict:
    return {
        "id": rule.pk,
        "name": rule.name,
        "modifier": rule.modifier,
        "target": rule.target,
        "is_active": rule.is_active,
        "automatic_applications": rule.automatic_applications,
        "priority": rule.priority,
        "min_order_amount": format_money(rule.min_order_amount),
        "free_shipping": rule.free_shipping,
        "free_order": rule.free_order,
        "channels": [c.idx for c in rule.channels.all()],
        "code_count": rule.code_count_anno if hasattr(rule, "code_count_anno") else rule.codes.count(),
        "created_at": rule.created_at,
    }


def serialize_rule_detail(rule: DiscountRuleCode) -> dict:
    return {
        "id": rule.pk,
        "name": rule.name,
        "modifier": rule.modifier,
        "extra_value": rule.extra_value,
        "target": rule.target,
        "min_order_amount": format_money(rule.min_order_amount),
        "free_shipping": rule.free_shipping,
        "free_shipping_methods": [m.code for m in rule.free_shipping_methods.all()],
        "free_order": rule.free_order,
        "is_omnibus": rule.is_omnibus,
        "is_active": rule.is_active,
        "show_when_invalid": rule.show_when_invalid,
        "combine_with_other_rules": rule.combine_with_other_rules,
        "automatic_applications": rule.automatic_applications,
        "priority": rule.priority,
        "extension": rule.extension or {},
        "channels": [c.idx for c in rule.channels.all()],
        "currencies": [c.iso3 for c in rule.currencies.all()],
        "created_at": rule.created_at,
        "code_count": rule.codes.count(),
        "product_filters": [serialize_product_filter(f) for f in rule.discount_mode_of_actions_rule.all()],
        "threshold_filters": [serialize_product_filter(f) for f in rule.threshold_filters.all()],
        "customer_filters": [serialize_customer_filter(f) for f in rule.discount_customer_mode_of_actions_rule.all()],
    }
