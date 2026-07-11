# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import Any

from django.core.exceptions import ObjectDoesNotExist
from process_logger import ProcessLogger

from .prices import determine_country_of_pricelist

logger_process = ProcessLogger("CHECKOUT_CART")


def fetch_custom_details(item, channel: "Channel", language_iso2=None):
    try:
        from django_pim.models import ProductAttribute, ProductCustom
    except ImportError:
        raise Exception("ProductAttribute model not found")

    try:
        from django_pricemanager.output_custom import get_product_custom_price_for_country_and_currency
    except ImportError:
        return {}
    try:
        product_custom = ProductCustom.objects.get(real_product__sku=item.sku, shop__idx=channel.idx)
    except ProductCustom.DoesNotExist:
        return {}

    all_attr_chosen = []
    if item.extra and isinstance(item.extra, dict):
        for _f, _v in item.extra.items():
            if isinstance(_v, str):
                all_attr_chosen.append(_v)
            elif isinstance(_v, list):
                all_attr_chosen.extend(v for v in _v if isinstance(v, str))

    try:
        attrs_choosen_or_default_custom, am_value, errors = ProductAttribute.objects.get_custom_attributes_idx(
            item.sku, all_attr_chosen, channel.idx, limit_view=True
        )
    except Exception:
        return {}
    try:
        attrs_full, _, _ = ProductAttribute.objects.get_custom_attributes_idx(
            item.sku, all_attr_chosen, channel.idx, limit_view=False
        )
        attrs_is_default = list(attrs_full.values_list("is_default", flat=True))
    except Exception:
        attrs_is_default = []

    product_attr_specification_pretty = {}
    feature_translations = {}

    for attr in attrs_choosen_or_default_custom.values(
        "feature__idx",
        f"feature__name_t9n__{language_iso2}",
        "idx",
        f"name_t9n__{language_iso2}",
        "is_default",
        "name_t9n__en",
    ):
        if attr["feature__idx"] not in product_attr_specification_pretty:
            product_attr_specification_pretty[attr["feature__idx"]] = []
        product_attr_specification_pretty[attr["feature__idx"]].append(
            {
                "idx": attr["idx"],
                "type": "choosen",
                "name": attr[f"name_t9n__{language_iso2}"],
                "name_en": attr["name_t9n__en"] if "name_t9n__en" in attr else "",
                "is_default": attr["is_default"],
            }
        )
        feature_translations[attr["feature__idx"]] = attr[f"feature__name_t9n__{language_iso2}"]

    product_attr_specification_sorted = ProductAttribute.objects.get_custom_attributes_details(
        item.sku, am_value, channel.idx, language_iso2, limit_view=True
    )
    feature_translations_default = feature_translations
    for f9e, attr in product_attr_specification_sorted.items():
        if f9e not in product_attr_specification_pretty:
            product_attr_specification_pretty[f9e] = []
        if attr[1]:
            feature_translations[f9e] = attr[1]
        if len(attr) > 4 and attr[4]:
            feature_translations_default[f9e] = attr[4]
        product_attr_specification_pretty[f9e].append(
            {"value": attr[0], "type": "calculated_details", "name": attr[1], "extra": attr[3] if len(attr) > 3 else {}}
        )
    from django_checkout.settings import USE_SOURCE_PRODUCT_WHILE_CONFIGURABLE_ALL_ATTS_DEFAULT

    result = {
        "custom": product_attr_specification_pretty,
        "custom_translated": feature_translations,
        "custom_translated_default": feature_translations_default,
    }
    if (
        USE_SOURCE_PRODUCT_WHILE_CONFIGURABLE_ALL_ATTS_DEFAULT
        and attrs_is_default
        and all(attrs_is_default)
        and product_custom.source_product_id is not None
    ):
        result["source_product_sku"] = product_custom.source_product.sku
    return result


def fetch_custom_prices(
    items, channel: "Channel", country_code, currency_code, validated_addresses=None, vat_0: bool = False, cart_id=None
) -> dict[Any, Any]:
    price_list_grouped_by_sku = {}
    country_code = determine_country_of_pricelist(country_code, validated_addresses)

    try:
        from django_pim.models import ProductAttribute, ProductCustom
    except ImportError:
        raise Exception("ProductAttribute model not found")

    try:
        from django_pricemanager.output_custom import get_product_custom_price_for_country_and_currency
    except ImportError:
        return {}

    for item in items:
        try:
            ProductCustom.objects.get(real_product__sku=item.sku, shop__idx=channel.idx)
        except ProductCustom.DoesNotExist:
            continue
        all_attr_chosen = []
        if item.extra and isinstance(item.extra, dict):
            for _f, _v in item.extra.items():
                if isinstance(_v, str):
                    all_attr_chosen.append(_v)
                elif isinstance(_v, list):
                    all_attr_chosen.extend(v for v in _v if isinstance(v, str))

        attrs_choosen_or_default_custom, am_value, errors = ProductAttribute.objects.get_custom_attributes_idx(
            item.sku, all_attr_chosen, channel.idx
        )
        cannot_be_sale = False
        for err in errors:
            if err.code == "required_features_missing":
                cannot_be_sale = True

        if cannot_be_sale:
            price_list_grouped_by_sku[item.sku_identifier] = None
            return price_list_grouped_by_sku

        attrs_choosen_or_default_custom = list(attrs_choosen_or_default_custom.values_list("idx", flat=True))

        try:
            price = get_product_custom_price_for_country_and_currency(
                item.sku, attrs_choosen_or_default_custom, channel.idx, country_code, currency_code, vat_0=vat_0
            )
            price_list_grouped_by_sku[item.sku_identifier] = price["price"]

            components = price.get("_price_components")
            if components and components["attributes"]:
                parts = [f"base gross={components['base_gross']} net={components['base_net']}"]
                for comp in components["attributes"]:
                    parts.append(f"attr {comp['attrs']} gross={comp['gross']} net={comp['net']}")
                parts.append(f"TOTAL gross={price['price'].get('gross')} net={price['price'].get('net')}")
                logger_process.add_log_param("cart_id", str(cart_id) if cart_id else None)
                logger_process.info(f"Custom price for SKU={item.sku} channel={channel.idx}: {' | '.join(parts)}")
                logger_process.delete_log_param("cart_id")

        except ObjectDoesNotExist:
            pass

    return price_list_grouped_by_sku
