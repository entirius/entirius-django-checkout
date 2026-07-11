# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import ClassVar

from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.domain.dto.discount import ValidatedDiscountData
from django_checkout.enums import ItemStatus


@add_schema
@dataclass
class DiscountItemData:
    sku: str = None
    quantity: Decimal = None
    Schema: ClassVar[type[Schema]] = Schema


@add_schema
@dataclass
class ExtendedItemData(DiscountItemData):
    discount_amount: Decimal | None = Decimal(0)
    base_unit_price: Decimal | None = None
    base_total_price: Decimal | None = None
    special_total_price: Decimal | None = None
    unit_price: Decimal | None = None
    total_price: Decimal | None = None
    tax_rate: Decimal | None = None
    unit_tax_amount: Decimal | None = None
    total_tax_amount: Decimal | None = None


@add_schema
@dataclass
class DiscountItems:
    items: list[DiscountItemData | ExtendedItemData] | None = None
    total_price: Decimal | None = None
    discount_amount: Decimal | None = Decimal(0)
    discounts: ValidatedDiscountData | None = None
    Schema: ClassVar[type[Schema]] = Schema

    @staticmethod
    def factory():
        return DiscountItems(
            items=None,
            discounts=[
                ValidatedDiscountData(
                    code=None,
                    item_code=None,
                    free_shipping=False,
                    free_shipping_methods=[],
                    price_discount=0,
                    percent_discount=0,
                    status=ItemStatus.INVALID,
                    free_order=False,
                    min_order_amount=0,
                    extra_value=0,
                    target=None,
                    modifier=None,
                    sku=None,
                    quantity=0,
                    clear_discounts=False,
                )
            ],
            total_price=0,
            discount_amount=0,
        )
