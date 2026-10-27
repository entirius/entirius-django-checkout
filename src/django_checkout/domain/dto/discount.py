# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import dataclasses
from dataclasses import field
from decimal import Decimal
from typing import ClassVar

import marshmallow.validate
from django_utils.api.errors import ErrorInfo
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


@dataclasses.dataclass
class DiscountsRejectedError(ErrorInfo):
    """``one_or_more_discount_invalid`` plus what v2 needs to report each rejected code.

    v1 renders it through ``ErrorInfo.to_dict()``, which knows none of the fields below, so v1 responses don't change.
    """

    rejected: list[ValidatedDiscountData] = field(default_factory=list, repr=False)
    sent_codes: list[str] = field(default_factory=list, repr=False)
    applied_codes: list[str] = field(default_factory=list, repr=False)


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
