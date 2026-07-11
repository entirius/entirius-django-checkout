# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Sync product stock to Checkout from QMS data.

Reads QMS warehouse stock and xray output, pushes to Checkout
ProductRepresentation + Stock via update_from_qms_dataset().

Sources (checked in order, quantities aggregated):
  1. QMS Warehouse — WarehouseStock rows for active warehouses serving the channel
  2. QMS XRay output — latest completed PIT for checkout output storage

Typical usage in seed pipeline (after QMS fill):
    manage.py sync-checkout-stock-from-qms
    manage.py sync-checkout-stock-from-qms --channel_idx default-europe
"""

from django.core.management.base import BaseCommand

from django_checkout.models import Channel, Stock


class Command(BaseCommand):
    help = "Sync Checkout ProductRepresentation + Stock from QMS warehouse and xray data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel_idx",
            type=str,
            default=None,
            help="Sync specific channel only (default: all channels)",
        )

    def handle(self, *args, **options):
        channel_idx = options["channel_idx"]

        if channel_idx:
            channels = Channel.objects.filter(idx=channel_idx)
        else:
            channels = Channel.objects.all()

        for channel in channels:
            dataset = self._build_dataset(channel)
            if not dataset["items"]:
                self.stdout.write(f"  {channel.idx}: no QMS data to sync")
                continue

            self._ensure_global_supplier(channel)
            Stock.objects.update_from_qms_dataset(dataset, channel.idx)
            self.stdout.write(self.style.SUCCESS(f"  {channel.idx}: synced {len(dataset['items'])} products"))

    def _build_dataset(self, channel) -> dict:
        """Build dataset from QMS warehouse stock + xray output."""
        items_by_sku: dict[str, int] = {}

        # Source 1: QMS Warehouse stock
        try:
            from django_qms.models import Warehouse, WarehouseStock

            for wh in Warehouse.objects.filter(is_active=True, channels__idx=channel.idx):
                for ws in WarehouseStock.objects.filter(warehouse=wh):
                    items_by_sku[ws.sku] = items_by_sku.get(ws.sku, 0) + ws.quantity
        except ImportError:
            pass

        # Source 2: QMS XRay output (latest completed PIT)
        try:
            from django_qms.models import XrayPointInTime, XrayQuantityByStorage, XrayStorage

            output_storage = XrayStorage.objects.filter(
                channel__idx=channel.idx,
                data_direction=XrayStorage.DataDirection.STORAGE_OUTPUT,
                storage_manager=XrayStorage.StorageManagerEnum.CHECKOUT,
            ).first()

            if output_storage:
                pit = (
                    XrayPointInTime.objects.filter(
                        channel__idx=channel.idx,
                        proces_status="D",
                    )
                    .order_by("-pit")
                    .first()
                )

                if pit:
                    for qty in XrayQuantityByStorage.objects.filter(pit=pit, storage=output_storage).select_related(
                        "product"
                    ):
                        sku = qty.product.sku
                        if sku not in items_by_sku:
                            items_by_sku[sku] = qty.quantity_int
        except ImportError:
            pass

        return {"items": [{"sku": sku, "quantity": qty} for sku, qty in items_by_sku.items()]}

    def _ensure_global_supplier(self, channel) -> None:
        """Ensure a global Supplier exists for the channel."""
        from django_checkout.models import Supplier

        if not Supplier.objects.filter(channel=channel, is_global=True).exists():
            Supplier.objects.create(
                code=f"seed-{channel.idx}",
                name=f"Seed Supplier ({channel.idx})",
                channel=channel,
                is_global=True,
            )
