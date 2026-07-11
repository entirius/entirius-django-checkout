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
from django_checkout.models.discount_rule_code import ModifiersForDiscountRule, TargetForDiscountRule


@add_schema
@dataclass
class DiscountData:
    code: str
    sku: str | None
    quantity: int | None
    clear_discounts: bool | None
    Schema: ClassVar[type[Schema]] = Schema

    @staticmethod
    def factory():
        return DiscountData(code="", sku="", quantity=0, clear_discounts=False)


@add_schema
@dataclass
class ValidatedDiscountData(DiscountData):
    item_code: str | list[str] | None
    price_discount: Decimal | None
    percent_discount: int | None
    free_shipping: bool
    free_shipping_methods: list[str] | None
    status: ItemStatus = field(metadata=dict(by_value=True))
    free_order: bool | None
    min_order_amount: float | None
    extra_value: dict | int | list | None
    target: str | None = field(metadata={"validate": marshmallow.validate.OneOf(TargetForDiscountRule.values)})
    modifier: str | None = field(metadata={"validate": marshmallow.validate.OneOf(ModifiersForDiscountRule.values)})
    is_automatic: bool = False
    rejection_reason: str | None = None
    rejection_meta: dict | None = None


@add_schema
@dataclass
class GratisRules:
    items: list[dict]
    code: str
    name: str
    price_missing_to_next_gratis_tier: str
    next_gratis_tier_quantity: int
    max_available_quantity: int
    next_gratis_tier_price: int
    all_tiers: dict
    is_available: bool = True
    extension: dict | None = None
