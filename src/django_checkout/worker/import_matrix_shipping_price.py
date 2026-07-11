# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pandas as pd
from django.db import transaction
from django.db.models import Q
from process_logger import ProcessLogger

from django_checkout.models import Channel, ShippingOption, ShippingPriceByWeight, ShippingPriceMatrix


class ImporterError(Exception):
    pass


class ShippingPriceMatrixRowImporter:
    def __init__(self, importer, row):
        self.importer: ShippingPriceMatrixImporter = importer
        self.row = row
        self.shipping_method_filters = []
        self.shipping_price_matrix = None

    def _get_country_options(self):
        country_iso2 = self.row["country"].upper()
        if not country_iso2 == "ALL":
            self.shipping_method_filters.append(Q(country__iso2=country_iso2))

    def _get_currency_options(self):
        currency_iso3 = self.row["currency"].upper()
        if not currency_iso3 == "ALL":
            self.shipping_method_filters.append(Q(currency__iso3=currency_iso3))

    def _get_shipping_options_for_shipping_method(self):
        self._get_country_options()
        self._get_currency_options()

        shipping_method_idx = self.row["shipping_method"]
        method_shipping_options = ShippingOption.objects.filter(
            method__code=shipping_method_idx,
            method__channel__idx=self.importer.channel_idx,
            *self.shipping_method_filters,
        )
        if not method_shipping_options:
            self.importer.logger.error(f"Shipping option with code {shipping_method_idx} not found")
            raise ImporterError(f"Shipping option with code {shipping_method_idx} not found")
        return method_shipping_options

    def _get_filter_type(self):
        filter_type = self.row["filter_type"].lower()
        type_exists = [True for ft in ShippingPriceMatrix.FilterType.choices if ft[0].lower() == filter_type]
        if not type_exists:
            self.importer.logger.error(f"Filter type {filter_type} not found")
            raise ImporterError(f"Filter type {filter_type} not found")

        if filter_type == ShippingPriceMatrix.FilterType.ATTR.lower():
            # check if idx and value are provided, not null
            if pd.isna(self.row["idx"]) or pd.isna(self.row["value"]):
                self.importer.logger.error(f"idx and value are required for filter type {filter_type}")
                raise ImporterError(f"idx and value are required for filter type {filter_type}")
        return filter_type

    def _check_data_row_is_empty(self):
        for col in self.importer.cols_required:
            if col not in self.row:
                self.importer.logger.error(f"Column {col} is required in CSV file")
                raise ImporterError(f"Column {col} is required in CSV file")

    def _create_shipping_price_matrix(self, option: ShippingOption):
        defaults = {}
        # check if idx and value are provided, not null
        idx = self.row["idx"] if pd.notna(self.row["idx"]) else None
        value = (self.row["value"]) if pd.notna(self.row["value"]) else None
        if pd.notna(self.row["free_delivery_above_brutto"]):
            defaults["free_delivery_above_brutto"] = self.row["free_delivery_above_brutto"]

        spm, is_created = ShippingPriceMatrix.objects.get_or_create(
            shipping_option=option, filter_type=self._get_filter_type(), idx=idx, value=value, defaults=defaults
        )
        return spm

    def _create_shipping_price_by_weight(self, spm: ShippingPriceMatrix):
        ShippingPriceByWeight.objects.get_or_create(
            shipping_price_matrix=spm, weight=self.row["weight"], defaults={"price_brutto": self.row["price"]}
        )

    def import_row(self):
        with transaction.atomic():
            try:
                self._check_data_row_is_empty()
                for option in self._get_shipping_options_for_shipping_method():
                    spm = self._create_shipping_price_matrix(option)
                    self._create_shipping_price_by_weight(spm)
                print(".")

            except ImporterError:
                print("E")
                return
            except Exception as e:
                self.importer.logger.exception(e)
                return


class ShippingPriceMatrixImporter:
    def __init__(self, file_path, channel_idx, delete_previous):
        if not file_path:
            raise Exception("File path is required")

        if delete_previous:
            ShippingPriceMatrix.objects.filter(shipping_option__method__channel__idx=channel_idx).delete()
            print(f"Previous data deleted for {channel_idx}")

        self.channel_idx = channel_idx
        self.file_path = file_path
        self.name = "Shipping Price Matrix CSV"
        self.cols = ["shipping_method", "currency", "country", "weight", "price", "filter_type", "idx", "value"]
        self.cols_required = ["shipping_method", "currency", "country", "weight", "price", "filter_type"]
        self.data: pd.DataFrame = pd.read_csv(file_path, dtype={"idx": str, "value": str})

        # pd.DataFrame it's a table with rows and columns. It is a 2-dimensional labeled data structure with columns of potentially different types. You can declare column type as above or automatically by pandas.
        #   shipping_method currency country  weight  price filter_type  free_delivery_above_brutto             idx value
        # 0     courier_dpd      ALL     ALL       0     10         all                        1200             NaN   NaN
        # 1     courier_dpd      ALL     ALL      10     15         all                        1200             NaN   NaN
        # in following code i use iterrows() to iterate over DataFrame rows as (index, Series) pairs, To get column value i use row["column_name"]
        # pandas have many functions to manipulate dataframes, like mering,sorting, filtering, droping blank rows etc.

        # really good basic explanation cheat sheet: https://pandas.pydata.org/Pandas_Cheat_Sheet.pdf

        self.logger = ProcessLogger("ShippingPriceMatrixImporter")
        self.logger.add_log_param("channel_idx", channel_idx)

    def _check_columns(self, data: pd.DataFrame):
        for col in self.cols_required:
            if col not in data.columns:
                raise Exception(f"Column {col} is required in CSV file")

    def _check_data(self):
        if self.data.empty:
            raise Exception("CSV file is empty")

    def _check_channel(self):
        if not self.channel_idx or not Channel.objects.filter(idx=self.channel_idx).exists():
            raise Exception(f"Channel with idx {self.channel_idx} not found")

    def _order_by_columns(self):
        self.data = self.data.sort_values(
            by=["currency", "country"], key=lambda col: col.map(lambda x: (x != "ALL", x))
        ).reset_index(drop=True)

    def start(self):
        self._check_columns(self.data)
        self._check_data()
        self._order_by_columns()
        for index, row in self.data.iterrows():
            self.logger.add_log_param_once("number", index)
            ShippingPriceMatrixRowImporter(self, row).import_row()
