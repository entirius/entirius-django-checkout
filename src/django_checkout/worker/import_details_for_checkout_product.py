# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import csv
import logging
from functools import wraps

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from tqdm import tqdm

from django_checkout.models import Channel, Stock

logger = logging.getLogger(__name__)


class ImportDetailsForCheckoutProduct:
    required_columns = ["sku", "saleable_quantity_limit_per_order"]
    all_skus = []
    csv_reader = None

    def __init__(self, channel_idx: str, file_path, command_cls):
        self.channel_idx = channel_idx
        self.file_path = file_path
        self.command_cls = command_cls

    def finish_failed(self, message):
        logger.error(message)
        self.command_cls.stdout.write(self.command_cls.style.ERROR(message))

    def check_csv_columns(self, csv_reader):
        if set(csv_reader.fieldnames) != set(self.required_columns):
            self.finish_failed(f"CSV file should have columns: {self.required_columns}")
            raise ValueError(f"CSV file should have columns: {self.required_columns}")

    def get_channel(self) -> Channel:
        try:
            return Channel.objects.get(idx=self.channel_idx)
        except ObjectDoesNotExist:
            self.finish_failed(f"Channel with idx {self.channel_idx} does not exist")
            raise ValueError(f"Channel with idx {self.channel_idx} does not exist")

    @staticmethod
    def get_product_stock(sku: str, channel: Channel) -> Stock:
        try:
            return Stock.objects.get(
                product__sku=sku, product__channel=channel, supplier__channel=channel, supplier__is_global=True
            )
        except ObjectDoesNotExist:
            raise ValueError(f"Product stock with sku {sku} does not exist")

    @staticmethod
    def with_opened_file(file_path_attr, mode="r"):
        def decorator(func):
            @wraps(func)
            def wrapper(self, *args, **kwargs):
                file_path = getattr(self, file_path_attr)
                with open(file_path, mode) as file:
                    self.csv_reader = csv.DictReader(file)
                    result = func(self, *args, **kwargs)
                return result

            return wrapper

        return decorator

    @with_opened_file("file_path")
    def import_saleable_quantity_limit_per_order(self):
        self.check_csv_columns(self.csv_reader)
        channel = self.get_channel()
        bulk_update_objs = []
        self.all_skus = []
        for row in tqdm(self.csv_reader):
            sku = row["sku"] if "sku" in row else None
            if not sku:
                continue
            try:
                saleable_quantity_limit_per_order = row["saleable_quantity_limit_per_order"]
                stock = self.get_product_stock(sku, channel)
                stock.saleable_quantity_limit_per_order = saleable_quantity_limit_per_order
                bulk_update_objs.append(stock)
                self.all_skus.append(sku)

            except Exception as e:
                logger.exception(e)
                logger.error(f"[{channel.idx}] Error while processing row {sku}")

        with transaction.atomic():
            try:
                Stock.objects.bulk_update(bulk_update_objs, ["saleable_quantity_limit_per_order"], batch_size=1000)
            except Exception as e:
                logger.exception(e)

        self.command_cls.stdout.write(
            self.command_cls.style.SUCCESS(f"Updated {len(self.all_skus)} product stock for channel {channel.idx}")
        )

        unprocessed_sku = Stock.objects.filter(
            product__channel=channel, supplier__channel=channel, supplier__is_global=True
        ).exclude(product__sku__in=self.all_skus)
        unprocessed_sku.update(saleable_quantity_limit_per_order=999999)
        self.command_cls.stdout.write(
            self.command_cls.style.SUCCESS(
                f"Set saleable_quantity_limit_per_order to 999999 for {len(unprocessed_sku)} product stock for channel {channel.idx}"
            )
        )

    def start(self):
        self.import_saleable_quantity_limit_per_order()
