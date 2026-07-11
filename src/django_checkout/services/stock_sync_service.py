# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sync warehouse stock changes to Checkout Stock records.

Called by the QMS warehouse_stock_changed signal handler.
This module has ZERO imports from django_qms — all data passed via arguments.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("django_checkout.stock_sync")


def sync_warehouse_to_checkout(
    *,
    channels: list,
    supplier_code: str,
    supplier_name: str,
    skus_with_quantities: dict[str, int],
) -> dict:
    """Propagate warehouse stock to Checkout Stock for given channels.

    Args:
        channels: List of Checkout Channel instances.
        supplier_code: Code for auto-created Supplier (e.g., "wh-warszawa").
        supplier_name: Display name for auto-created Supplier.
        skus_with_quantities: {sku: quantity} dict from WarehouseStock.

    Returns:
        Report dict with counts.
    """
    from django_checkout.models import Stock

    if not channels:
        return {"channels": 0, "stocks_upserted": 0}

    skus = list(skus_with_quantities.keys())
    report = {"channels": len(channels), "stocks_upserted": 0}

    # Step 1: Ensure Supplier exists per channel
    suppliers_by_channel = _ensure_suppliers(channels, supplier_code, supplier_name)

    # Step 2: Ensure ProductRepresentation exists per (channel, sku)
    pr_map = _ensure_product_representations(channels, skus)

    # Step 3: Pre-fetch ALL existing stocks across all suppliers in one query
    supplier_ids = [s.pk for s in suppliers_by_channel.values()]
    existing_stocks = {}
    if supplier_ids:
        for s in Stock.objects.filter(supplier_id__in=supplier_ids, product__sku__in=skus).select_related("product"):
            existing_stocks[(s.supplier_id, s.product.sku.lower())] = s

    # Step 4: Build stock upserts — pure Python loop, no per-channel queries
    all_to_create = []
    all_to_update = []
    for channel in channels:
        supplier = suppliers_by_channel.get(channel.pk)
        if not supplier:
            continue

        for sku in skus:
            pr = pr_map.get((channel.pk, sku.lower()))
            if not pr:
                continue
            quantity = skus_with_quantities.get(sku, 0)
            stock_key = (supplier.pk, sku.lower())

            if stock_key in existing_stocks:
                stock = existing_stocks[stock_key]
                if stock.quantity != quantity:
                    stock.quantity = quantity
                    all_to_update.append(stock)
            else:
                all_to_create.append(Stock(product=pr, supplier=supplier, quantity=quantity))

    if all_to_create:
        Stock.objects.bulk_create(all_to_create, batch_size=500)
    if all_to_update:
        Stock.objects.bulk_update(all_to_update, fields=["quantity"], batch_size=500)

    report["stocks_upserted"] = len(all_to_create) + len(all_to_update)
    logger.info("Synced %s -> %d channels, %d stocks", supplier_code, report["channels"], report["stocks_upserted"])
    return report


def _ensure_suppliers(channels, supplier_code: str, supplier_name: str) -> dict:
    """Get the global Supplier per channel. Warehouse stock writes to the existing global supplier
    so Cynthia (which reads from is_global=True) sees the aggregated quantity.

    Returns {channel_pk: Supplier}.
    """
    from django_checkout.models import Supplier

    result = {}
    for ch in channels:
        supplier = Supplier.objects.filter(channel=ch, is_global=True).first()
        if supplier:
            result[ch.pk] = supplier
        else:
            logger.warning("No global supplier for channel %s — skipping stock sync", ch.idx)
    return result


def _ensure_product_representations(channels, skus: list[str]) -> dict:
    """Ensure ProductRepresentation exists per (channel, sku). Returns {(channel_pk, lower_sku): PR}."""
    from django_checkout.models import ProductRepresentation

    # Fetch existing
    pr_map = {}
    for pr in ProductRepresentation.objects.filter(channel__in=channels, sku__in=skus):
        pr_map[(pr.channel_id, pr.sku.lower())] = pr

    # Find and create missing combos
    to_create = []
    for channel in channels:
        for sku in skus:
            if (channel.pk, sku.lower()) not in pr_map:
                to_create.append(ProductRepresentation(sku=sku, channel=channel))

    if to_create:
        ProductRepresentation.objects.bulk_create(to_create, ignore_conflicts=True)
        # Re-fetch only the missing ones
        missing_channel_ids = {ch.pk for ch in channels}
        for pr in ProductRepresentation.objects.filter(channel_id__in=missing_channel_ids, sku__in=skus):
            pr_map[(pr.channel_id, pr.sku.lower())] = pr

    return pr_map
