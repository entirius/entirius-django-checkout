# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OrderCreateResponse(BaseModel):
    """Response for POST orders/ — order creation."""

    order_id: str = Field(description="Order UUID")
    order_pretty_id: str = Field(description="Human-readable order ID", examples=["0100001"])
    order_status: str = Field(description="Initial order status", examples=["UNPAID"])
    redirect_url: str | None = Field(None, description="Payment provider redirect URL")
    split_orders_pretty_ids: list[str] = Field(default_factory=list, description="IDs of split orders if applicable")


class OrderAttachmentResponse(BaseModel):
    file_id: int = Field(description="Attachment ID")
    name: str | None = Field(None, description="Attachment filename")


class PaymentMethodSummary(BaseModel):
    """One payment method used to settle an order.

    An order can carry several — a voucher covering part of the total settles alongside
    the gateway that covers the rest.
    """

    code: str = Field(description="Payment method code", examples=["banktransfer"])
    name: str | None = Field(
        None, description="Payment method name in the requested language", examples=["Bank Transfer"]
    )


class OrderListItemResponse(BaseModel):
    """Slim order summary for GET orders/list/."""

    order_id: str = Field(description="Order UUID")
    pretty_id: str = Field(description="Human-readable order ID", examples=["0100001"])
    status: str = Field(description="Current order status", examples=["CONFIRMED"])
    status_label: str | None = Field(None, description="Translated status label")
    created: datetime | None = Field(None, description="Order creation timestamp")
    updated: datetime | None = Field(None, description="Last update timestamp")

    total_gross: str | None = Field(None, description="Order total gross", examples=["419.97"])
    total_net: str | None = Field(None, description="Order total net", examples=["341.44"])
    total_tax: str | None = Field(None, description="Order total tax", examples=["78.53"])
    currency: str | None = Field(None, description="Currency code", examples=["EUR"])
    country_code: str | None = Field(None, description="Order country code", examples=["DE"])

    item_count: int = Field(0, description="Number of line items")
    shipping_method_code: str | None = Field(None, description="Selected shipping method")
    payment_methods: list[PaymentMethodSummary] = Field(
        default_factory=list,
        description="Payment methods that settled the order — several when a voucher is combined with a gateway",
    )

    attachments: list[OrderAttachmentResponse] = Field(default_factory=list, description="Order attachments")


class OrderListResponse(BaseModel):
    """Paginated order list."""

    count: int = Field(description="Total number of orders")
    next: str | None = Field(None, description="Next page URL")
    previous: str | None = Field(None, description="Previous page URL")
    results: list[OrderListItemResponse] = Field(default_factory=list)
