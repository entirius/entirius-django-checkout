# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import field
from decimal import Decimal
from typing import ClassVar

import marshmallow.validate
from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.enums import ItemStatus


@add_schema
@dataclass
class ShippingData:
    code: str
    name: str | None
    country_code: str | None
    delivery_point: dict | None
    Schema: ClassVar[type[Schema]] = Schema


@add_schema
@dataclass
class ValidatedShippingData(ShippingData):
    base_unit_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    base_total_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    discount_amount: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    unit_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total_price: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    cash_on_delivery_fee: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    free_delivery_above: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    free_delivery_above_modifier: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    status: ItemStatus = field(metadata=dict(by_value=True))
    tax_rate: Decimal | None = field(default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)})
    unit_price_netto: Decimal | None = field(
        default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    total_price_netto: Decimal | None = field(
        default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    tax_amount: Decimal | None = field(default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)})
    normal_price: Decimal | None = field(default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)})
    modifier_price: Decimal | None = field(
        default=Decimal(0.0), metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    free_delivery_items: list[str] = field(default_factory=list)
    free_delivery_modifier_items: list[str] = field(default_factory=list)
