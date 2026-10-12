# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db.models import Case, IntegerField, Value, When
from django_pim.models import ProductBundle
from django_pim.services.bundle_data import get_option_titles
from django_utils.api.responses import ErrorInfo

from django_checkout.domain.bundle import bundle_data_factory
from django_checkout.domain.dto.item import ItemData, SkuQuantityData
from django_checkout.models import Stock

if TYPE_CHECKING:
    from django_accounts.models import Customer

    from django_checkout.models import Channel


def _fetch_bundle_limits(sku_list: list) -> dict:
    limits = {}
    for bundle in ProductBundle.objects.filter(real_product__sku__in=sku_list):
        sku = bundle.real_product.sku
        entry = {}
        raw_max = bundle.get_max_limit_bundle()
        if raw_max is not None:
            try:
                entry["max"] = int(raw_max)
            except (ValueError, TypeError):
                pass
        raw_min = bundle.get_min_limit_bundle()
        if raw_min is not None:
            try:
                entry["min"] = int(raw_min)
            except (ValueError, TypeError):
                pass
        if entry:
            limits[sku] = entry
    return limits


def check_which_bundle_is_not_saleable(
    sku_list, channel, items_data, customer: "Customer" = None, language: str | None = None
):
    bundles = bundle_data_factory(items_list=sku_list)
    bundle_limits = _fetch_bundle_limits(sku_list)
    all_sub_skus = [sub_sku for subproducts in bundles.values() for sub_sku in subproducts.keys()]
    option_titles = get_option_titles(channel.idx, all_sub_skus, language) if language and all_sub_skus else {}
    not_saleable_bundles = {}
    bundle_errors: list[ErrorInfo] = []
    items_dict = {}
    bundle_items = {}

    for item in items_data:
        if isinstance(item, ItemData):
            key, value = next(iter(item.dict_factory().items()))
            items_dict[key] = value

    frontend_selections = {
        item.sku: {sub.sku: sub.quantity for sub in item.sub_items}
        for item in items_data
        if isinstance(item, ItemData) and item.sub_items
    }

    supplier_customers = customer.customer_suppliers.filter(supplier__channel=channel).first() if customer else None
    supplier = supplier_customers.supplier if supplier_customers else None

    for bundle_sku, subproducts_data in bundles.items():
        frontend_selection = frontend_selections.get(bundle_sku)
        active_subproducts = {}

        bundle_limit = bundle_limits.get(bundle_sku)
        has_limits = bool(bundle_limit)

        if has_limits:
            if frontend_selection:
                unknown_skus = [sku for sku in frontend_selection if sku not in subproducts_data]
                if unknown_skus:
                    bundle_errors.append(
                        ErrorInfo(
                            code="bundle_sub_items_not_in_bundle",
                            message="Sub-items are not part of this bundle",
                            affected_values=unknown_skus,
                            affected_field="cart.items.sub_items",
                            extra={"bundle_sku": bundle_sku},
                        )
                    )
                for sub_sku, meta in subproducts_data.items():
                    if meta["is_required"]:
                        active_subproducts[sub_sku] = meta["quantity"]
                    elif sub_sku in frontend_selection:
                        qty = frontend_selection[sub_sku] if meta["can_change_quantity"] else meta["quantity"]
                        active_subproducts[sub_sku] = qty
            else:
                for sub_sku, meta in subproducts_data.items():
                    if meta["is_required"] or meta["is_default"]:
                        active_subproducts[sub_sku] = meta["quantity"]
        else:
            for sub_sku, meta in subproducts_data.items():
                active_subproducts[sub_sku] = meta["quantity"]

        if not active_subproducts:
            not_saleable_bundles[bundle_sku] = {"is_saleable": False, "saleable_quantity": 0}
            bundle_items[bundle_sku] = []
            continue

        if bundle_limit:
            optional_qty = sum(active_subproducts.values())
            max_limit = bundle_limit.get("max")
            min_limit = bundle_limit.get("min")
            if max_limit is not None and optional_qty > max_limit:
                bundle_errors.append(
                    ErrorInfo(
                        code="bundle_sub_items_exceeded_limit",
                        message="Bundle sub-items quantity exceeds the allowed maximum",
                        affected_values=[bundle_sku],
                        affected_field="cart.items.sub_items",
                        extra={"max_limit": max_limit, "current_qty": optional_qty},
                    )
                )
                not_saleable_bundles[bundle_sku] = {"is_saleable": False, "saleable_quantity": 0}
                bundle_items[bundle_sku] = []
                continue
            if min_limit is not None and optional_qty < min_limit:
                bundle_errors.append(
                    ErrorInfo(
                        code="bundle_sub_items_below_min_limit",
                        message="Bundle sub-items quantity is below the required minimum",
                        affected_values=[bundle_sku],
                        affected_field="cart.items.sub_items",
                        extra={"min_limit": min_limit, "current_qty": optional_qty},
                    )
                )
                not_saleable_bundles[bundle_sku] = {"is_saleable": False, "saleable_quantity": 0}
                bundle_items[bundle_sku] = []
                continue

        bundle_qty = items_dict.get(bundle_sku, 0)
        cases = [When(product__sku=sku, then=Value(qty)) for sku, qty in active_subproducts.items()]
        query = (
            Stock.annotated.filter(
                product__sku__in=active_subproducts.keys(),
                product__channel=channel,
                supplier__channel=channel,
                supplier__is_global=True,
            )
            .values("product__sku", "saleable_quantity", "is_saleable")
            .annotate(quantity=Case(*cases, output_field=IntegerField(), default=0))
        )
        quantity_grouped_by_sku = {
            elem["product__sku"]: {k: v for k, v in elem.items() if k != "product__sku"} for elem in query
        }

        if supplier:
            query_customer = (
                Stock.annotated.filter(
                    product__sku__in=active_subproducts.keys(), product__channel=channel, supplier=supplier
                )
                .values("product__sku", "saleable_quantity", "is_saleable")
                .annotate(quantity=Case(*cases, output_field=IntegerField(), default=0))
            )
            for elem in query_customer:
                sku = elem["product__sku"]
                quantity_grouped_by_sku[sku] = {k: v for k, v in elem.items() if k != "product__sku"}

        is_saleable = []
        saleable = []
        bundle_items[bundle_sku] = []

        for sku, item in quantity_grouped_by_sku.items():
            if bundle_qty != 0:
                is_saleable.append(item["saleable_quantity"] >= item["quantity"] * bundle_qty)
                saleable.append(item["saleable_quantity"] // item["quantity"])
                bundle_items[bundle_sku].append(
                    SkuQuantityData(
                        sku=sku,
                        quantity=item["quantity"] * bundle_qty,
                        option_title=option_titles.get(sku),
                    )
                )
            else:
                is_saleable.append(False)
                saleable.append(0)

        not_saleable_bundles[bundle_sku] = {
            "is_saleable": all(is_saleable),
            "saleable_quantity": min(saleable) if saleable else 0,
        }

    return not_saleable_bundles, bundle_items, bundle_errors


def fetch_quantities(sku_list: list[str], channel: "Channel", customer: "Customer" = None) -> dict:
    sku_list = [elem for elem in sku_list]
    query = Stock.annotated.filter(
        product__sku__in=sku_list, product__channel=channel, supplier__channel=channel, supplier__is_global=True
    ).values("product__sku", "saleable_quantity", "is_saleable")
    quantity_grouped_by_sku = {
        elem["product__sku"]: {k: v for k, v in elem.items() if k != "product__sku"} for elem in query
    }

    supplier_customers = customer.customer_suppliers.filter(supplier__channel=channel).first() if customer else None
    supplier = supplier_customers.supplier if supplier_customers else None
    if supplier:
        query_customer = Stock.annotated.filter(
            product__sku__in=sku_list, product__channel=channel, supplier=supplier
        ).values("product__sku", "saleable_quantity", "is_saleable")

        for elem in query_customer:
            sku = elem["product__sku"]
            quantity_grouped_by_sku[sku] = {k: v for k, v in elem.items() if k != "product__sku"}

    return quantity_grouped_by_sku
