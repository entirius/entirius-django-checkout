# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import ClassVar

from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.domain.dto.order import OrderData


@add_schema
@dataclass
class StockData:
    product: str | None
    quantity: str | None
    saleable_quantity_limit: int | None
    Schema: ClassVar[type[Schema]] = Schema


@add_schema
@dataclass
class StockReservationData:
    reserved_quantity: int | None
    stock: StockData
    order: OrderData
    created: str | None
    updated: str | None
    Schema: ClassVar[type[Schema]] = Schema
