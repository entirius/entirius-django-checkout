# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Admin request schemas for discount rules, codes, and filters.

Flexible `extra_value` pass-through: int / dict / list accepted as-is. The shape
depends on `modifier` (see field description); correctness is enforced at
calc-time in worker/discount_calc.py, not here. `modifier`/`target`/inclusion
values are validated against their enums in the service layer (schemas MUST NOT
import Django models).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class DiscountCodeInput(BaseModel):
    """A single coupon code under a rule. `current_used` is read-only (server-managed)."""

    code: str = Field(min_length=1, max_length=40, description="Coupon code customers type", examples=["SUMMER20"])
    max_used: int = Field(1, ge=1, description="Max total redemptions for this code", examples=[100])
    max_uses_per_user: int | None = Field(
        None, ge=1, description="Max redemptions per customer (null = unlimited)", examples=[1]
    )
    active_from: date | None = Field(
        None, description="Code valid from (null = no lower bound)", examples=["2026-06-01"]
    )
    active_to: date | None = Field(None, description="Code valid to (null = no upper bound)", examples=["2026-06-30"])
    max_products_qty: int | None = Field(
        None, ge=1, description="Block code when any line qty exceeds this (null = no limit)", examples=[10]
    )

    @model_validator(mode="after")
    def _check_date_window(self) -> DiscountCodeInput:
        if self.active_from and self.active_to and self.active_to < self.active_from:
            raise ValueError("active_to must be on or after active_from")
        return self


class DiscountCodeUpdateInput(BaseModel):
    """Partial update of a code. Only provided fields change."""

    code: str | None = Field(None, min_length=1, max_length=40, description="Coupon code", examples=["SUMMER20"])
    max_used: int | None = Field(None, ge=1, description="Max total redemptions", examples=[100])
    max_uses_per_user: int | None = Field(None, ge=1, description="Max redemptions per customer", examples=[1])
    active_from: date | None = Field(None, description="Code valid from", examples=["2026-06-01"])
    active_to: date | None = Field(None, description="Code valid to", examples=["2026-06-30"])
    max_products_qty: int | None = Field(None, ge=1, description="Max line qty before code is blocked", examples=[10])


class ProductFilterInput(BaseModel):
    """Product filter for a rule. Used for both product filters (which products get the
    discount / are eligible as gratis) and threshold filters (which products count toward
    a gratis threshold). M2M references resolve by business identifier; unknown ones are
    ignored. Products/categories are scoped to the URL channel's shop.
    """

    is_inclusion_or_exclusion: str = Field(
        "inclusion", description="'inclusion' or 'exclusion'", examples=["inclusion"]
    )
    take_common_part: bool = Field(False, description="AND across criteria when true, OR when false", examples=[False])
    attributes: list[str] = Field(default_factory=list, description="PIM Attribute idx list", examples=[["color-red"]])
    features_qty_greater_than_attr_value: list[str] = Field(
        default_factory=list, description="PIM Feature idx list (qty > attribute value)", examples=[["pack-size"]]
    )
    features_qty_is_multiple_of_attr_value: list[str] = Field(
        default_factory=list,
        description="PIM Feature idx list (qty multiple of attribute value)",
        examples=[["pack-size"]],
    )
    categories: list[str] = Field(
        default_factory=list, description="PIM ProductCategory idx list", examples=[["socks"]]
    )
    products: list[str] = Field(default_factory=list, description="Product SKU list", examples=[["ENT-S004"]])
    product_price_from: int | None = Field(
        None, ge=0, description="Min unit price for a product to qualify", examples=[50]
    )
    product_price_to: int | None = Field(
        None, ge=0, description="Max unit price for a product to qualify", examples=[500]
    )
    cart_price_from: int | None = Field(None, ge=0, description="Min cart total for the filter to act", examples=[200])
    cart_price_to: int | None = Field(None, ge=0, description="Max cart total for the filter to act", examples=[1000])
    qty_from: int | None = Field(None, ge=0, description="Min line quantity to qualify", examples=[2])
    qty_to: int | None = Field(None, ge=0, description="Max line quantity to qualify", examples=[10])
    cart_qty_from: int | None = Field(
        None, ge=0, description="Min total cart quantity for the filter to act", examples=[3]
    )
    cart_qty_to: int | None = Field(
        None, ge=0, description="Max total cart quantity for the filter to act", examples=[20]
    )


class CustomerFilterInput(BaseModel):
    """Customer/group targeting for a rule. Customers reference accounts Customer `uid`,
    groups reference accounts Group `code`. Unknown references are ignored.
    """

    is_inclusion_or_exclusion: str = Field(
        "inclusion", description="'inclusion' or 'exclusion'", examples=["inclusion"]
    )
    take_common_part: bool = Field(False, description="AND across criteria when true, OR when false", examples=[False])
    customers: list[str] = Field(
        default_factory=list, description="Accounts Customer uid (UUID) list", examples=[["3f1e...uuid"]]
    )
    groups: list[str] = Field(default_factory=list, description="Accounts Group code list", examples=[["vip"]])


class DiscountRuleCreateInput(BaseModel):
    """Create a discount rule. The URL channel is always added to `channels`."""

    name: str | None = Field(None, max_length=128, description="Internal rule name", examples=["Summer -20%"])
    modifier: str = Field(
        description="Discount mechanism (see ModifiersForDiscountRule)", examples=["percent_discount"]
    )
    extra_value: int | dict | list = Field(
        default_factory=dict,
        description="Modifier config. int for percent/price_discount; threshold map for step_*; {sku,sku_logic,quantity} for gratis_by_sku_in_cart; int qty for cheapest/most_expensive_gratis. Per-currency dicts supported.",
        examples=[10],
    )
    target: str = Field(
        "all", description="Eligibility target (all / first_order_logged / first_order_all)", examples=["all"]
    )
    min_order_amount: Decimal = Field(
        Decimal("0.00"), ge=0, description="Min cart value for the rule to act", examples=["199.00"]
    )
    free_shipping: bool = Field(False, description="Rule grants free shipping", examples=[False])
    free_order: bool = Field(False, description="Rule makes the whole order free", examples=[False])
    is_omnibus: bool = Field(False, description="Rule qualifies for omnibus price recalculation", examples=[False])
    is_active: bool = Field(True, description="Rule is enabled", examples=[True])
    show_when_invalid: bool = Field(
        True, description="Show gratis rule in /gratis-rules/ even when conditions are unmet", examples=[True]
    )
    combine_with_other_rules: bool = Field(False, description="Allow stacking with other rules", examples=[False])
    automatic_applications: bool = Field(False, description="Apply automatically without a code", examples=[False])
    priority: int | None = Field(
        None, ge=0, description="Lower wins; null falls back to created_at order", examples=[10]
    )
    extension: dict = Field(
        default_factory=dict, description="Free-form JSON (marketing copy, descriptions)", examples=[{}]
    )
    channels: list[str] = Field(
        default_factory=list, description="Channel idx list (URL channel auto-added)", examples=[["b2c-pl"]]
    )
    currencies: list[str] = Field(
        default_factory=list, description="Currency iso3 list (empty = all)", examples=[["EUR", "PLN"]]
    )
    free_shipping_methods: list[str] = Field(
        default_factory=list, description="Shipping method codes qualifying for free shipping", examples=[["inpost"]]
    )
    codes: list[DiscountCodeInput] = Field(default_factory=list, description="Optional codes created with the rule")


class DiscountRuleBulkRequest(BaseModel):
    """Bulk activate / deactivate / delete on discount rules — by ids, or all matching the list filters."""

    action: str = Field(description="One of: activate | deactivate | delete", examples=["activate"])
    ids: list[int] = Field(
        default_factory=list, description="Rule PKs (ignored when all_matching=true)", examples=[[1, 2, 3]]
    )
    all_matching: bool = Field(
        False, description="Apply to ALL rules matching `filters` across every page (not just `ids`)", examples=[False]
    )
    filters: dict = Field(
        default_factory=dict,
        description="Same list filters (search, is_active, automatic, modifier, target) — used only when all_matching=true",
        examples=[{"is_active": "false"}],
    )


class DiscountRuleUpdateInput(BaseModel):
    """Partial update of a rule. Only provided fields change. Manage codes via the codes
    sub-resource, not here.
    """

    name: str | None = Field(None, max_length=128, description="Internal rule name", examples=["Summer -20%"])
    modifier: str | None = Field(None, description="Discount mechanism", examples=["percent_discount"])
    extra_value: int | dict | list | None = Field(
        None, description="Modifier config (see create schema)", examples=[10]
    )
    target: str | None = Field(None, description="Eligibility target", examples=["all"])
    min_order_amount: Decimal | None = Field(
        None, ge=0, description="Min cart value for the rule to act", examples=["199.00"]
    )
    free_shipping: bool | None = Field(None, description="Rule grants free shipping", examples=[False])
    free_order: bool | None = Field(None, description="Rule makes the whole order free", examples=[False])
    is_omnibus: bool | None = Field(None, description="Rule qualifies for omnibus recalculation", examples=[False])
    is_active: bool | None = Field(None, description="Rule is enabled", examples=[True])
    show_when_invalid: bool | None = Field(None, description="Show gratis rule when conditions unmet", examples=[True])
    combine_with_other_rules: bool | None = Field(None, description="Allow stacking", examples=[False])
    automatic_applications: bool | None = Field(None, description="Apply without a code", examples=[False])
    priority: int | None = Field(None, ge=0, description="Lower wins", examples=[10])
    extension: dict | None = Field(None, description="Free-form JSON", examples=[{}])
    channels: list[str] | None = Field(None, description="Channel idx list (URL channel kept)", examples=[["b2c-pl"]])
    currencies: list[str] | None = Field(None, description="Currency iso3 list", examples=[["EUR"]])
    free_shipping_methods: list[str] | None = Field(None, description="Shipping method codes", examples=[["inpost"]])
