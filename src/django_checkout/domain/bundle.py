# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from itertools import groupby
from operator import itemgetter

from django_pim.models.product_bundle.bundle_link import BundleLink
from django_pim.services.bundle_data import (
    get_bundle_limits,
    get_bundle_subproducts,
    get_default_subproducts,
)
from django_pricemanager.output_bundle import get_aggregated_bundle_price

from django_checkout.domain.dto.item import ItemData
from django_checkout.domain.prices import fetch_prices

# Subproduct flags added to BundleLink in newer django-pim. Older pim (<= migration
# 0045) lacks them — default to "always required, bundle-defined quantity" so bundle
# saleability validation behaves exactly as it did before the flags existed.
_OPTIONAL_BUNDLE_LINK_FIELDS = {
    "is_required": True,
    "is_default": True,
    "can_change_quantity": False,
}


def get_grouped_data(bundle_links_data):
    grouped_data = {}
    for key, group in groupby(bundle_links_data, key=itemgetter("product_bundle__real_product__sku")):
        subproduct_mapping = {
            item["subproduct__real_product__sku"]: {
                "quantity": item["quantity"],
                **{field: item.get(field, default) for field, default in _OPTIONAL_BUNDLE_LINK_FIELDS.items()},
            }
            for item in group
        }
        grouped_data[key] = subproduct_mapping
    return grouped_data


def bundle_data_factory(items: list[ItemData] = None, items_list: list = None):
    if items_list and not items:
        sku_list = items_list
    else:
        sku_list = [item.sku for item in items] if items else []

    bundle_links = BundleLink.objects.filter(product_bundle__real_product__sku__in=sku_list)
    model_fields = {f.name for f in BundleLink._meta.get_fields()}
    optional_fields = [f for f in _OPTIONAL_BUNDLE_LINK_FIELDS if f in model_fields]
    bundle_links_data = bundle_links.values(
        "product_bundle__real_product__sku",
        "subproduct__real_product__sku",
        "quantity",
        *optional_fields,
    )

    grouped_data = get_grouped_data(list(bundle_links_data))
    return grouped_data


def get_active_subproducts(
    subproducts_data: dict,
    bundle_limit: dict | None,
    frontend_selection: dict | None,
) -> dict:
    """Checkout's add-to-cart selection rule. Returns {sub_sku: qty_per_one_bundle}.

    - Legacy bundle (no limits) → all subproducts × their BundleLink.quantity.
    - Ranged bundle, no frontend selection → defers to PIM's pure rule
      (is_required OR is_default).
    - Ranged bundle, with frontend selection → is_required (always) + frontend-picked SKUs.
      Per-sku quantity comes from frontend if can_change_quantity=True, else from BundleLink.
    """
    if not bundle_limit:
        return {sub_sku: meta["quantity"] for sub_sku, meta in subproducts_data.items()}
    if not frontend_selection:
        return get_default_subproducts(subproducts_data)

    active: dict = {}
    for sub_sku, meta in subproducts_data.items():
        if meta["is_required"]:
            active[sub_sku] = meta["quantity"]
        elif sub_sku in frontend_selection:
            qty = frontend_selection[sub_sku] if meta["can_change_quantity"] else meta["quantity"]
            active[sub_sku] = qty
    return active


def resolve_bundle_prices(
    items: list,
    channel,
    country: str,
    currency: str,
    prices_by_sku: dict,
    uid: str | None = None,
) -> dict:
    """For each ranged bundle item in `items`, replace its entry in `prices_by_sku`
    with a price aggregated from its active subproducts (frontend selection or
    defaults). Legacy bundles (no min/max limit) are left untouched so existing
    fixed-price flows keep working.

    Mutates and returns `prices_by_sku`.
    """
    if not items:
        return prices_by_sku

    sku_list = [item.sku for item in items if hasattr(item, "sku")]
    if not sku_list:
        return prices_by_sku

    bundles_data = get_bundle_subproducts(channel.idx, sku_list)
    if not bundles_data:
        return prices_by_sku

    bundle_limits = get_bundle_limits(channel.idx, sku_list)
    if not bundle_limits:
        return prices_by_sku

    for item in items:
        if not isinstance(item, ItemData):
            continue
        bundle_sku = item.sku
        subproducts_data = bundles_data.get(bundle_sku)
        if not subproducts_data:
            continue
        bundle_limit = bundle_limits.get(bundle_sku)
        if not bundle_limit:
            # Legacy bundle — keep its own pricelist row, do not aggregate.
            continue

        frontend_selection = (
            {sub.sku: sub.quantity for sub in item.sub_items} if getattr(item, "sub_items", None) else None
        )
        active = get_active_subproducts(subproducts_data, bundle_limit, frontend_selection)
        if not active:
            continue

        snapshot, _verified_only = get_aggregated_bundle_price(
            channel_idx=channel.idx,
            country_code=country,
            currency_code=currency,
            bundle_sku=bundle_sku,
            components_quantities=active,
            uid=uid,
        )
        if snapshot is None:
            continue
        prices_by_sku[item.sku_identifier] = _snapshot_to_standard_price_dict(snapshot, bundle_sku, uid)

    return prices_by_sku


def _snapshot_to_standard_price_dict(snapshot: dict, bundle_sku: str, uid: str | None) -> dict:
    """Map pricemanager's aggregated snapshot to the dict shape produced by
    Price.get_standard_price() — what fetch_prices uses as values in prices_by_sku.
    """
    return {
        "product": bundle_sku,
        "tax_class": None,
        "gross": snapshot["gross"],
        "net": snapshot["net"],
        "special_gross": snapshot["special_gross"],
        "special_net": snapshot["special_net"],
        "tax_rate": snapshot["tax_rate"],
        "special_from_date": snapshot["special_from_date"],
        "special_to_date": snapshot["special_to_date"],
        "is_egible_for_special_price": snapshot["has_special_price"],
        "uid": uid,
    }


def validate_bundle_price_vs_products_price(bundles, channel, country, currency):
    """
    Deprecated
    """
    bundle_price_higher = []
    no_bundle_price = []
    validation_result = []
    started_bundle = False
    for bundle_sku, subproducts in bundles:
        started_bundle = True
        subproducts_skus = [str(product["subproduct__real_product__sku"]) for product in subproducts]
        skus = [bundle_sku, *subproducts_skus]
        bundle_prices, country_code = fetch_prices(
            sku_list=skus,
            channel=channel,
            country_code=country,
            currency_code=currency,
            validated_addresses=None,
            uid=None,
        )
        sum_of_subproducts = 0
        for product, data in bundle_prices.items():
            if product in subproducts_skus:
                sum_of_subproducts += data["special_gross"] if data["special_gross"] else data["gross"]

        bundle_price = bundle_prices.get(bundle_sku, None)

        if not bundle_price:
            validation_result.append(False)
            no_bundle_price.append(bundle_sku)
            continue
        else:
            bundle_price = bundle_price["special_gross"] if bundle_price["special_gross"] else bundle_price["gross"]

        if bundle_price > sum_of_subproducts:
            validation_result.append(False)
            bundle_price_higher.append(bundle_sku)
        else:
            validation_result.append(True)

    if not started_bundle:
        return True, ""
    else:
        if all(validation_result):
            return True, ""
        else:
            msg = ""
            if no_bundle_price:
                msg += f" There is no bundle sku in price manager: {''.join(no_bundle_price)}. "
            if bundle_price_higher:
                msg += f" Bundles price: {''.join(bundle_price_higher)} higher than sum of subproducts. "

            return False, msg
