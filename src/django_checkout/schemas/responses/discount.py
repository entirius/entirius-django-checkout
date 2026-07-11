# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Admin response schemas for discount rules, codes, and filters.

These document the JSON shapes returned by the admin API (used by @extend_schema for
OpenAPI). The service layer builds matching dicts; monetary values are decimal strings.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class DiscountCodeResponse(BaseModel):
    id: int = Field(description="Code PK", examples=[42])
    code: str = Field(description="Coupon code", examples=["SUMMER20"])
    max_used: int = Field(description="Max total redemptions", examples=[100])
    max_uses_per_user: int | None = Field(None, description="Max redemptions per customer", examples=[1])
    current_used: int = Field(description="Redemptions so far (read-only)", examples=[7])
    active_from: date | None = Field(None, description="Valid from", examples=["2026-06-01"])
    active_to: date | None = Field(None, description="Valid to", examples=["2026-06-30"])
    max_products_qty: int | None = Field(None, description="Line qty cap before code is blocked", examples=[10])


class ProductFilterResponse(BaseModel):
    id: int = Field(description="Filter PK", examples=[7])
    is_inclusion_or_exclusion: str = Field(description="'inclusion' or 'exclusion'", examples=["inclusion"])
    take_common_part: bool = Field(description="AND across criteria when true", examples=[False])
    attributes: list[str] = Field(default_factory=list, description="PIM Attribute idx list", examples=[["color-red"]])
    features_qty_greater_than_attr_value: list[str] = Field(
        default_factory=list, description="PIM Feature idx list", examples=[["pack-size"]]
    )
    features_qty_is_multiple_of_attr_value: list[str] = Field(
        default_factory=list, description="PIM Feature idx list", examples=[["pack-size"]]
    )
    categories: list[str] = Field(
        default_factory=list, description="PIM ProductCategory idx list", examples=[["socks"]]
    )
    products: list[str] = Field(default_factory=list, description="Product SKU list", examples=[["ENT-S004"]])
    product_price_from: int | None = Field(None, description="Min unit price to qualify", examples=[50])
    product_price_to: int | None = Field(None, description="Max unit price to qualify", examples=[500])
    cart_price_from: int | None = Field(None, description="Min cart total for filter to act", examples=[200])
    cart_price_to: int | None = Field(None, description="Max cart total for filter to act", examples=[1000])
    qty_from: int | None = Field(None, description="Min line qty to qualify", examples=[2])
    qty_to: int | None = Field(None, description="Max line qty to qualify", examples=[10])
    cart_qty_from: int | None = Field(None, description="Min total cart qty for filter to act", examples=[3])
    cart_qty_to: int | None = Field(None, description="Max total cart qty for filter to act", examples=[20])


class CustomerFilterResponse(BaseModel):
    id: int = Field(description="Filter PK", examples=[3])
    is_inclusion_or_exclusion: str = Field(description="'inclusion' or 'exclusion'", examples=["inclusion"])
    take_common_part: bool = Field(description="AND across criteria when true", examples=[False])
    customers: list[str] = Field(
        default_factory=list, description="Accounts Customer uid list", examples=[["3f1e...uuid"]]
    )
    groups: list[str] = Field(default_factory=list, description="Accounts Group code list", examples=[["vip"]])


class DiscountRuleListItemResponse(BaseModel):
    """Slim rule summary for the list endpoint."""

    id: int = Field(description="Rule PK", examples=[12])
    name: str | None = Field(None, description="Internal rule name", examples=["Summer -20%"])
    modifier: str = Field(description="Discount mechanism", examples=["percent_discount"])
    target: str = Field(description="Eligibility target", examples=["all"])
    is_active: bool = Field(description="Rule is enabled", examples=[True])
    automatic_applications: bool = Field(description="Applies without a code", examples=[False])
    priority: int | None = Field(None, description="Lower wins", examples=[10])
    min_order_amount: str | None = Field(None, description="Min cart value (decimal string)", examples=["199.00"])
    free_shipping: bool = Field(description="Grants free shipping", examples=[False])
    free_order: bool = Field(description="Makes the order free", examples=[False])
    channels: list[str] = Field(default_factory=list, description="Channel idx list", examples=[["b2c-pl"]])
    code_count: int = Field(description="Number of codes under the rule", examples=[3])
    created_at: datetime | None = Field(None, description="Rule creation timestamp")


class DiscountRuleDetailResponse(BaseModel):
    """Full rule with nested codes and filters."""

    id: int = Field(description="Rule PK", examples=[12])
    name: str | None = Field(None, description="Internal rule name", examples=["Summer -20%"])
    modifier: str = Field(description="Discount mechanism", examples=["percent_discount"])
    extra_value: int | dict | list | None = Field(None, description="Modifier config", examples=[10])
    target: str = Field(description="Eligibility target", examples=["all"])
    min_order_amount: str | None = Field(None, description="Min cart value (decimal string)", examples=["199.00"])
    free_shipping: bool = Field(description="Grants free shipping", examples=[False])
    free_shipping_methods: list[str] = Field(
        default_factory=list, description="Free-shipping method codes", examples=[["inpost"]]
    )
    free_order: bool = Field(description="Makes the order free", examples=[False])
    is_omnibus: bool = Field(description="Qualifies for omnibus recalculation", examples=[False])
    is_active: bool = Field(description="Rule is enabled", examples=[True])
    show_when_invalid: bool = Field(description="Show gratis rule when conditions unmet", examples=[True])
    combine_with_other_rules: bool = Field(description="Allow stacking", examples=[False])
    automatic_applications: bool = Field(description="Applies without a code", examples=[False])
    priority: int | None = Field(None, description="Lower wins", examples=[10])
    extension: dict = Field(default_factory=dict, description="Free-form JSON", examples=[{}])
    channels: list[str] = Field(default_factory=list, description="Channel idx list", examples=[["b2c-pl"]])
    currencies: list[str] = Field(
        default_factory=list, description="Currency iso3 list (empty = all)", examples=[["EUR"]]
    )
    created_at: datetime | None = Field(None, description="Rule creation timestamp")
    code_count: int = Field(
        0,
        description="Number of codes under the rule (fetch them via the paginated codes sub-resource)",
        examples=[1342],
    )
    product_filters: list[ProductFilterResponse] = Field(
        default_factory=list, description="Product eligibility filters"
    )
    threshold_filters: list[ProductFilterResponse] = Field(
        default_factory=list, description="Gratis-threshold product filters"
    )
    customer_filters: list[CustomerFilterResponse] = Field(default_factory=list, description="Customer/group targeting")


class DiscountRuleBulkResponse(BaseModel):
    """Result of a bulk activate/deactivate/delete."""

    affected: int = Field(description="Number of rules affected", examples=[12])


class DiscountRuleListResponse(BaseModel):
    """Paginated rule list."""

    count: int = Field(description="Total rules matching filters", examples=[42])
    next: str | None = Field(None, description="Next page URL")
    previous: str | None = Field(None, description="Previous page URL")
    results: list[DiscountRuleListItemResponse] = Field(default_factory=list)


class DiscountCodeListResponse(BaseModel):
    """Paginated codes for a rule (a rule can have thousands of codes)."""

    count: int = Field(description="Total codes matching the filters", examples=[1342])
    next: str | None = Field(None, description="Next page URL")
    previous: str | None = Field(None, description="Previous page URL")
    results: list[DiscountCodeResponse] = Field(default_factory=list)


class ProductFilterListResponse(BaseModel):
    results: list[ProductFilterResponse] = Field(default_factory=list)


class CustomerFilterListResponse(BaseModel):
    results: list[CustomerFilterResponse] = Field(default_factory=list)


class MetaChoice(BaseModel):
    value: str = Field(description="Enum value to send back to the API", examples=["all"])
    label: str = Field(description="Human-readable label (translated)", examples=["Discount for everyone."])


class ModifierChoice(MetaChoice):
    extra_value_kind: str = Field(
        description="CMS form hint for extra_value: none | percent | amount | threshold_percent | threshold_amount | currency_threshold_price | gratis_qty | gratis_threshold | gratis_sku | json",
        examples=["percent"],
    )


class DiscountMetaResponse(BaseModel):
    """Choices + extra_value form hints so the CMS renders the right rule form per modifier."""

    modifiers: list[ModifierChoice] = Field(
        default_factory=list, description="Discount mechanisms with extra_value hint"
    )
    targets: list[MetaChoice] = Field(default_factory=list, description="Eligibility targets")
    inclusion_modes: list[MetaChoice] = Field(default_factory=list, description="inclusion / exclusion for filters")


class ShippingMethodOption(BaseModel):
    code: str = Field(description="Shipping method code (value stored in free_shipping_methods)", examples=["dhl"])
    channel_idx: str = Field(description="Channel idx the method belongs to", examples=["b2c-pl"])
    name: str | None = Field(None, description="Display name (translated, best-effort)", examples=["DHL Courier"])


class ShippingMethodListResponse(BaseModel):
    """Shipping methods for a channel — options for the rule's free-shipping multi-select."""

    results: list[ShippingMethodOption] = Field(default_factory=list)


class CurrencyOption(BaseModel):
    iso3: str = Field(
        description="ISO 4217 currency code (value stored in currencies / per-currency keys)", examples=["EUR"]
    )
    name: str | None = Field(None, description="Currency display name", examples=["Euro"])
    symbol: str | None = Field(None, description="Currency symbol", examples=["€"])


class CurrencyListResponse(BaseModel):
    """Currencies for a channel — options for the rule's currencies multi-select and per-currency thresholds."""

    results: list[CurrencyOption] = Field(default_factory=list)


class ChannelOption(BaseModel):
    idx: str = Field(description="Channel idx (value stored in the rule's channels list)", examples=["b2c-pl"])
    name: str | None = Field(None, description="Channel display label", examples=["B2C Poland"])


class ChannelListResponse(BaseModel):
    """All checkout channels — options for the rule's channels multi-select."""

    results: list[ChannelOption] = Field(default_factory=list)
