# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import json
from decimal import Decimal
from functools import wraps

from django.core.cache import cache
from django.db import models
from django.db.models import Q
from django_pim.models import Product, ProductAttribute

from django_checkout.enums import AssociationChoices, AuthenticationState
from django_checkout.settings import LIMIT_PRODUCT_CACHE_TIME


def cache_function_result(timeout=None):
    """
    Dekorator cachujący wyniki funkcji na podstawie nazwy funkcji i wszystkich jej parametrów.

    Args:
        timeout: Czas życia cache w sekundach. Jeśli None, używa LIMIT_PRODUCT_CACHE_TIME
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_timeout = timeout if timeout is not None else LIMIT_PRODUCT_CACHE_TIME

            cache_data = {"function_name": func.__name__, "args": [], "kwargs": kwargs}

            def _key_for(arg):
                if hasattr(arg, "pk"):
                    return f"model_{arg.__class__.__name__}_{arg.pk}"
                if hasattr(arg, "values_list"):
                    try:
                        return f"queryset_{arg.query}"
                    except (AttributeError, ValueError):
                        return f"queryset_{type(arg).__name__}"
                if isinstance(arg, (list, tuple)):
                    return sorted(arg) if all(isinstance(x, (str, int, float)) for x in arg) else list(arg)
                return arg

            for arg in args:
                cache_data["args"].append(_key_for(arg))
            for key, value in kwargs.items():
                cache_data["kwargs"][key] = _key_for(value)

            cache_string = json.dumps(cache_data, sort_keys=True, default=str)
            cache_hash = hashlib.md5(cache_string.encode()).hexdigest()
            cache_key = f"func_cache_{func.__name__}_{cache_hash}"

            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result

            result = func(*args, **kwargs)
            cache.set(cache_key, result, timeout=cache_timeout)

            return result

        return wrapper

    return decorator


def _allowed_skus_for_sm(sku_list, sku_meta, missing_skus, sm_limits):
    """In-memory equivalent of `get_products_by_filters(..., is_negation=True)`.

    Returns the subset of `sku_list` not excluded by any limit. Mirrors the
    original SQL filter tree exactly:
      ~Q(real_product__sku__in=sku_filter)
      & ~Q(categories__idx__in=category_idx_filter)
      & ~Q(product_class__in=product_class_filter)
      & ~Q(real_product__sku__in=sku_limited_by_attr)
      & ~Q(feature_set__idx__in=feature_set_filter)
    """
    forbidden_skus = set()
    forbidden_categories = set()
    forbidden_product_classes = set()
    forbidden_attribute_idxes = set()
    forbidden_feature_sets = set()
    attribute_value_filters = []  # list of (feature_idx, parsed_value)

    for limit in sm_limits:
        association = limit.association_type
        if association == AssociationChoices.SKU:
            forbidden_skus.add(limit.idx)
        elif association == AssociationChoices.CATEGORY_IDX:
            forbidden_categories.add(limit.idx)
        elif association == AssociationChoices.PRODUCT_CLASS:
            # PRODUCT_CLASS stores integer values in `idx` (per enum docstring).
            try:
                forbidden_product_classes.add(int(limit.idx))
            except (TypeError, ValueError):
                pass
        elif association == AssociationChoices.ATTRIBUTE_IDX:
            forbidden_attribute_idxes.add(limit.idx)
        elif association == AssociationChoices.FEATURE_SET_IDX:
            forbidden_feature_sets.add(limit.idx)
        elif association == AssociationChoices.ATTRIBUTE_VALUE:
            value_str = limit.value or ""
            if value_str.lower() == "true":
                parsed = True
            elif value_str.lower() == "false":
                parsed = False
            elif value_str.isnumeric():
                parsed = Decimal(value_str)
            else:
                parsed = value_str
            attribute_value_filters.append((limit.idx, parsed))

    allowed = []
    for sku in sku_list:
        if sku in missing_skus:
            continue
        meta = sku_meta.get(sku)
        if meta is None:
            continue
        if sku in forbidden_skus:
            continue
        if meta["product_class"] is not None and meta["product_class"] in forbidden_product_classes:
            continue
        if meta["feature_set_idx"] is not None and meta["feature_set_idx"] in forbidden_feature_sets:
            continue
        if meta["category_idxes"] & forbidden_categories:
            continue
        if meta["attribute_idxes"] & forbidden_attribute_idxes:
            continue
        # ATTRIBUTE_VALUE: original `Q(*attribute_value_filter_query)` is an AND
        # of per-filter Qs, so a single ProductAttribute row must satisfy every
        # filter at once. With >1 filter targeting distinct feature_idx this is
        # impossible; preserve that exact behaviour.
        if attribute_value_filters and _sku_matches_av_filters(meta["feature_values"], attribute_value_filters):
            continue
        allowed.append(sku)
    return set(allowed)


def _sku_matches_av_filters(feature_values, attribute_value_filters):
    """True iff any single feature_values row satisfies ALL attribute_value
    filters simultaneously (AND semantics from original `Q(*filters)`)."""
    for f_idx, value_decimal, value_bool in feature_values:
        matches_all = True
        for limit_idx, parsed in attribute_value_filters:
            if f_idx != limit_idx:
                matches_all = False
                break
            if isinstance(parsed, bool):
                if value_bool is not parsed:
                    matches_all = False
                    break
            elif isinstance(parsed, Decimal):
                if value_decimal != parsed:
                    matches_all = False
                    break
            else:
                # Non-bool, non-decimal: original SQL compares against
                # value_decimal/value_bool — never matches a string.
                matches_all = False
                break
        if matches_all:
            return True
    return False


class ProductShipping(models.Manager):
    def get_base_queryset(self, shipping_method, payment_method, get_all=False):
        filtered_products = self.filter(is_active=True)

        if get_all:
            methods_query = Q()
        else:
            shipping_method_query = (
                Q(Q(shipping_method=shipping_method) & Q(payment_method__isnull=True)) if shipping_method else Q()
            )
            payment_method_query = (
                Q(Q(payment_method=payment_method) & Q(shipping_method__isnull=True)) if payment_method else Q()
            )
            payment_shipping_query = (
                (Q(shipping_method=shipping_method) & Q(payment_method=payment_method))
                if (shipping_method and payment_method)
                else Q()
            )

            methods_query = Q(
                Q(shipping_method_query | payment_method_query | payment_shipping_query)
                | Q(Q(shipping_method__isnull=True) & Q(payment_method__isnull=True))
            )

        all_filters = filtered_products.filter(methods_query)
        return all_filters

    @staticmethod
    @cache_function_result()
    def get_all_association_idxes(all_filters, sku_list, is_logged, cart_product_ids=None):

        if is_logged:
            logged_filter = Q(
                Q(authentication_state=AuthenticationState.ALL) | Q(authentication_state=AuthenticationState.LOGGED)
            )
        else:
            logged_filter = Q(
                Q(authentication_state=AuthenticationState.ALL) | Q(authentication_state=AuthenticationState.GUEST)
            )

        all_filters = all_filters.filter(logged_filter)

        sku_filter = all_filters.filter(association_type=AssociationChoices.SKU)
        category_idx_filter = all_filters.filter(association_type=AssociationChoices.CATEGORY_IDX)
        attribute_idx_filter = all_filters.filter(association_type=AssociationChoices.ATTRIBUTE_IDX)
        product_class_filter = all_filters.filter(association_type=AssociationChoices.PRODUCT_CLASS)
        attribute_value_filter = all_filters.filter(association_type=AssociationChoices.ATTRIBUTE_VALUE).select_related(
            "shipping_method", "payment_method"
        )
        feature_set_filter = all_filters.filter(association_type=AssociationChoices.FEATURE_SET_IDX)

        attribute_value_filter_query = []
        for attr in attribute_value_filter:
            value = (
                True
                if attr.value.lower() == "true"
                else (
                    False
                    if attr.value.lower() == "false"
                    else Decimal(attr.value)
                    if attr.value.isnumeric()
                    else attr.value
                )
            )
            if value is None:
                continue
            attribute_value_filter_query.append(
                Q(Q(value_decimal=value) | Q(value_bool=value)) & Q(feature__idx=attr.idx)
            )

        sku_filter = sku_filter.values_list("idx", flat=True)
        category_idx_filter = category_idx_filter.values_list("idx", flat=True)
        attribute_idx_filter = attribute_idx_filter.values_list("idx", flat=True)
        product_class_filter = list(product_class_filter.values_list("idx", flat=True))
        feature_set_filter = list(feature_set_filter.values_list("idx", flat=True))

        if cart_product_ids is None:
            cart_product_ids = list(Product.objects.filter(real_product__sku__in=sku_list).values_list("id", flat=True))
        sku_limited_by_attr = list(
            ProductAttribute.objects.filter(
                Q(Q(*attribute_value_filter_query) | Q(attribute__idx__in=attribute_idx_filter))
                | Q(product__product_class__in=product_class_filter) & Q(product__real_product__sku__in=sku_list),
                product_id__in=cart_product_ids,
            )
            .distinct()
            .values_list("product__real_product__sku", flat=True)
        )

        return sku_filter, category_idx_filter, product_class_filter, sku_limited_by_attr, feature_set_filter

    @staticmethod
    def get_products_by_filters(
        sku_filter,
        category_idx_filter,
        product_class_filter,
        sku_limited_by_attr,
        feature_set_filter,
        channel_idx,
        sku_list,
        is_negation=True,
    ):
        filters = Q()

        if category_idx_filter:
            if is_negation:
                filters &= ~Q(categories__idx__in=category_idx_filter)
            else:
                filters |= Q(categories__idx__in=category_idx_filter) if category_idx_filter.exists() else Q()

        if sku_filter:
            if is_negation:
                filters &= ~Q(real_product__sku__in=sku_filter)
            else:
                filters |= Q(real_product__sku__in=sku_filter)

        if product_class_filter:
            if is_negation:
                filters &= ~Q(product_class__in=product_class_filter)
            else:
                filters |= Q(product_class__in=product_class_filter)

        if sku_limited_by_attr:
            if is_negation:
                filters &= ~Q(real_product__sku__in=sku_limited_by_attr)
            else:
                filters |= Q(real_product__sku__in=sku_limited_by_attr)

        if feature_set_filter:
            if is_negation:
                filters &= ~Q(feature_set__idx__in=feature_set_filter)
            else:
                filters |= Q(feature_set__idx__in=feature_set_filter)
        data = (
            Product.objects.filter(filters, shop__idx=channel_idx, real_product__sku__in=sku_list)
            .select_related("real_product", "shop")
            .values_list("real_product__sku", flat=True)
            .distinct()
        )
        return data


class ProductLimit(ProductShipping):
    @cache_function_result()
    def filter_products_by_limitation(
        self, sku_list, shipping_method, payment_method, channel, is_logged, cart_product_ids=None
    ):
        """This method return products that are not limited by shipping method"""

        sku_list = sku_list if isinstance(sku_list, list) else []
        all_filters = self.get_base_queryset(shipping_method, payment_method)
        allowed_guest_checkout = True

        params = self.get_all_association_idxes(all_filters, sku_list, is_logged, cart_product_ids)
        product_data = self.get_products_by_filters(*params, channel.idx, sku_list, is_negation=True)

        if set(sku_list) != set(product_data):
            allowed_guest_checkout = False

        return product_data, allowed_guest_checkout

    def bulk_allowed_skus_per_sm(self, sku_list, channel, shipping_methods, is_logged):
        """Bulk equivalent of calling `filter_products_by_limitation` per shipping
        method. Returns dict[shipping_method_pk -> set(allowed_skus)].

        Replaces a 14-iteration loop (each doing 5-8 queries) with three bulk
        queries: LimitedProducts, Product+categories, ProductAttribute. The
        filtering logic that used to run in SQL via `Q(...) & ~Q(...)` is
        evaluated in Python against pre-fetched metadata.
        """
        from collections import defaultdict

        sku_set = set(sku_list)
        sm_pks = [sm.pk for sm in shipping_methods]
        if not sku_set or not sm_pks:
            return {pk: set() for pk in sm_pks}

        if is_logged:
            auth_states = [AuthenticationState.ALL, AuthenticationState.LOGGED]
        else:
            auth_states = [AuthenticationState.ALL, AuthenticationState.GUEST]

        all_limits = list(
            self.filter(is_active=True, payment_method__isnull=True, authentication_state__in=auth_states).filter(
                Q(shipping_method__isnull=True) | Q(shipping_method_id__in=sm_pks)
            )
        )
        if not all_limits:
            return {pk: set(sku_set) for pk in sm_pks}

        products = list(
            Product.objects.filter(real_product__sku__in=sku_list, shop__idx=channel.idx)
            .select_related("real_product", "feature_set")
            .prefetch_related("categories")
        )
        sku_meta = {}
        for product in products:
            sku_meta[product.real_product.sku] = {
                "product_class": product.product_class,
                "feature_set_idx": product.feature_set.idx if product.feature_set_id else None,
                "category_idxes": {category.idx for category in product.categories.all()},
                "attribute_idxes": set(),
                "feature_values": [],
            }

        missing_skus = sku_set - sku_meta.keys()

        cart_product_ids = [p.id for p in products]
        if cart_product_ids:
            pa_rows = ProductAttribute.objects.filter(product_id__in=cart_product_ids).values_list(
                "product__real_product__sku",
                "attribute__idx",
                "feature__idx",
                "value_decimal",
                "value_bool",
            )
            for sku, attr_idx, feature_idx, value_decimal, value_bool in pa_rows:
                meta = sku_meta.get(sku)
                if meta is None:
                    continue
                if attr_idx:
                    meta["attribute_idxes"].add(attr_idx)
                if feature_idx:
                    meta["feature_values"].append((feature_idx, value_decimal, value_bool))

        limits_by_sm = defaultdict(list)
        for limit in all_limits:
            limits_by_sm[limit.shipping_method_id].append(limit)
        global_limits = limits_by_sm.get(None, [])
        result = {}
        for sm in shipping_methods:
            sm_limits = limits_by_sm.get(sm.pk, []) + global_limits
            result[sm.pk] = _allowed_skus_for_sm(sku_list, sku_meta, missing_skus, sm_limits)
        return result


class ProductLink(ProductShipping):
    def filter_products_by_linked(
        self, sku_list, shipping_method, payment_method, channel, is_logged, cart_product_ids=None
    ):
        sku_list = sku_list if isinstance(sku_list, list) else []
        all_filters = self.get_base_queryset(shipping_method, payment_method)
        params = self.get_all_association_idxes(all_filters, sku_list, is_logged, cart_product_ids)
        return list(self.get_products_by_filters(*params, channel.idx, sku_list, is_negation=False))
