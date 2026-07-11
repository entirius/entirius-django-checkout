# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
from dataclasses import field
from datetime import date
from decimal import Decimal
from itertools import groupby
from typing import ClassVar

import marshmallow.validate
from marshmallow import Schema, fields, pre_load
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.domain.dto.address import AddressData
from django_checkout.domain.dto.cart import CartData, _wrap_legacy_payment_method_dict
from django_checkout.domain.dto.payment import ValidatedPaymentData
from django_checkout.domain.dto.shipping import ValidatedShippingData
from django_checkout.enums import ValidationStatus
from django_checkout.utils import anonymize


@add_schema
@dataclass
class OrderRequest:
    cart_id: str
    Schema: ClassVar[type[Schema]] = Schema


@add_schema
@dataclass
class OrderData:
    cart: CartData
    addresses: AddressData
    payment_method: list[ValidatedPaymentData] | None
    shipping_method: ValidatedShippingData
    total_to_min_order_price: str | None
    fee_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total_tax: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    base_total: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    language_code: str = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    currency_code: str = field(metadata={"validate": marshmallow.validate.Length(equal=3)})
    country_code: str = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    validation_status: ValidationStatus = field(metadata=dict(by_value=True))
    requested_delivery_date: date | None
    split_order: bool | None
    split_by_feature_idx: str | None
    split_by_attr_idx: str | None
    original_order_id: str | None
    need_full_address: bool | None
    custom_order_id: str | None = field(metadata={"validate": marshmallow.validate.Length(max=80)})
    fee_tax_price: Decimal = field(
        default=Decimal(0),
        metadata={"validate": marshmallow.validate.Range(min=0), "load_default": Decimal(0), "required": False},
    )
    fee_tax_rate: Decimal = field(
        default=Decimal(0),
        metadata={"validate": marshmallow.validate.Range(min=0), "load_default": Decimal(0), "required": False},
    )
    comment: str | None = None
    affiliate_code: str | None = None
    customer_ip: str | None = None
    Schema: ClassVar[type[Schema]] = Schema

    @pre_load
    def _normalize_payment_method(self, data, **kwargs):
        return _wrap_legacy_payment_method_dict(data)

    @property
    def primary_payment_method(self):
        pm = self.payment_method
        if isinstance(pm, list):
            return pm[0] if pm else None
        return pm

    def anonymize(self) -> bool:
        save = False
        if self.addresses is not None:
            if self.addresses.billing_address is not None:
                self.addresses.billing_address.email = anonymize(self.addresses.billing_address.email, "email")
                if hasattr(self.addresses.billing_address, "firstname"):
                    self.addresses.billing_address.firstname = anonymize(
                        self.addresses.billing_address.firstname, "str"
                    )
                if hasattr(self.addresses.billing_address, "lastname"):
                    self.addresses.billing_address.lastname = anonymize(self.addresses.billing_address.lastname, "str")
                save = True
            if self.addresses.shipping_address is not None:
                self.addresses.shipping_address.email = anonymize(self.addresses.shipping_address.email, "email")
                if hasattr(self.addresses.shipping_address, "firstname"):
                    self.addresses.shipping_address.firstname = anonymize(
                        self.addresses.shipping_address.firstname, "str"
                    )
                if hasattr(self.addresses.shipping_address, "lastname"):
                    self.addresses.shipping_address.lastname = anonymize(
                        self.addresses.shipping_address.lastname, "str"
                    )
                save = True
        return save


@add_schema
@dataclass
class OrderResponse:
    order_id: str
    order_status: str
    Schema: ClassVar[type[Schema]] = Schema


@dataclass
class Sorter:
    order: str
    Schema: ClassVar[type[Schema]] = Schema


@dataclass
class FieldSorter(Sorter):
    field: str
    Schema: ClassVar[type[Schema]] = Schema


@dataclass
class OrderParams:
    lang: str | None = None
    order_id: str | None = None
    page: int | None = None
    limit: int | None = None
    product_name: str | None = None
    order_status: str | None = None
    sort: list[FieldSorter] | None = None
    returnable: bool | None = None
    Schema: ClassVar[type[Schema]] = Schema

    @pre_load
    def handle_sort(self, data, **kwargs):
        if "sort" in data:
            result = {**data}
            result["sort"] = [json.loads(elem) for elem in result.get("sort")]
            return result
        return data

    @pre_load
    def handle_parameter_list(self, data, **kwargs):
        """Aggregate values from list like parameters, for example from sku and sku[], into one field"""
        key_func = lambda x: x.strip("[]")
        grouped = groupby(sorted(data, key=key_func), key=key_func)
        result = {}
        for key, group in grouped:
            acc = []
            for elem in group:
                acc.extend(data.getlist(elem))

            field = self.declared_fields.get(key)
            if field is None:
                continue
            if isinstance(field, fields.List):
                result[key] = acc
            else:
                result[key] = acc[0]
        return result
