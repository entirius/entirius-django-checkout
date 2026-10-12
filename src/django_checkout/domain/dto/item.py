# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import json
from dataclasses import field
from decimal import Decimal
from typing import ClassVar

import marshmallow.validate
from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.enums import ItemStatus


@add_schema
@dataclass
class SkuQuantityData:
    sku: str
    quantity: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    option_title: str | None = None


@add_schema
@dataclass
class ItemData:
    sku: str
    quantity: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    offer_price: Decimal | None
    extra: dict | None
    sub_items: list[SkuQuantityData] | None
    # Gift-card personalization + design choice. kw_only keeps the positional ctor
    # and ValidatedItemData's non-default fields intact; default=None makes the
    # marshmallow field optional so pre-existing cart bodies still load.
    # Deliberately NOT part of sku_identifier/extra — must stay price-neutral.
    voucher_gift: dict | None = field(default=None, kw_only=True)
    Schema: ClassVar[type[Schema]] = Schema

    def dict_factory(self):
        return {self.sku: self.quantity}

    @property
    def sku_identifier(self):
        # for price custom reason
        if not self.extra:
            return self.sku

        try:
            payload = json.dumps(self.extra, sort_keys=True, separators=(",", ":"))
        except Exception:
            payload = str(self.extra)

        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return f"{self.sku}:{digest}"


@add_schema
@dataclass
class GratisData:
    sku: str
    quantity: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    extra: dict | None = None
    is_gratis: bool = False

    @property
    def sku_identifier(self):
        return self.sku


@add_schema
@dataclass
class ValidatedItemData(ItemData):
    discount_amount: Decimal
    special_from_date: str | None
    special_to_date: str | None
    discount_percent: int | None

    name: str
    base_unit_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    base_total_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    special_unit_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    special_total_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    unit_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    status: ItemStatus = field(metadata=dict(by_value=True))
    tax_rate: Decimal | None = field(default=None, metadata={"validate": marshmallow.validate.Range(min=0)})
    total_weight: Decimal | None = field(default=None, metadata={"validate": marshmallow.validate.Range(min=0)})
    unit_tax_amount: Decimal = field(default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)})
    total_tax_amount: Decimal = field(default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)})
    base_unit_tax_amount: Decimal = field(
        default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    base_total_tax_amount: Decimal = field(
        default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    special_percent: int | None = None
    unit_price_netto: Decimal | None = None
    total_price_netto: Decimal | None = None
    is_gratis: bool = False
    discount_amount_netto: Decimal | None = None

    @staticmethod
    def invalid_factory(sku, status, is_gratis, quantity, name="", voucher_gift=None):
        return ValidatedItemData(
            sku=sku,
            quantity=quantity,
            extra={},
            voucher_gift=voucher_gift,
            discount_amount=0,
            offer_price=0,
            special_from_date="",
            special_to_date="",
            discount_percent=0,
            name=name,
            base_unit_price=0,
            base_total_price=0,
            special_unit_price=0,
            special_total_price=0,
            unit_price=0,
            total_price=0,
            status=status,
            tax_rate=0,
            unit_tax_amount=0,
            total_tax_amount=0,
            base_unit_tax_amount=0,
            base_total_tax_amount=0,
            sub_items=None,
            is_gratis=is_gratis,
            special_percent=0,
            unit_price_netto=0,
            total_price_netto=0,
            discount_amount_netto=0,
        )
