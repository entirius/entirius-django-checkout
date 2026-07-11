# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from itertools import groupby
from operator import itemgetter

from django_pim.models.product_bundle.bundle_link import BundleLink

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
