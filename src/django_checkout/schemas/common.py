# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from pydantic import BaseModel, Field


def format_money(value: Decimal | int | float | None) -> str | None:
    """Format monetary value as fixed-precision string. Returns None for None input."""
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def format_tax_rate(value: Decimal | int | float | None) -> str | None:
    """Convert tax rate to decimal string. Handles both int (23) and decimal (0.23) input."""
    if value is None:
        return None
    d = Decimal(str(value))
    if d > 1:
        d = d / Decimal("100")
    return str(d.quantize(Decimal("0.01")))


class V2ErrorDetail(BaseModel):
    scope: str = Field(description="Sub-resource scope", examples=["items"])
    field: str | None = Field(None, description="JSON path to affected field", examples=["items[0].quantity"])
    code: str = Field(description="Machine-readable error code", examples=["ITEM_OUT_OF_STOCK"])
    message: str = Field(description="Human-readable error message")
    meta: dict | None = Field(None, description="Context for smart frontend recovery")


class V2ErrorResponse(BaseModel):
    error: str = Field(description="Error category", examples=["VALIDATION_ERROR"])
    message: str = Field(description="Summary message", examples=["Cart validation failed."])
    debug_id: str = Field(description="Unique ID for support", examples=["f7a2c3b8"])
    details: list[V2ErrorDetail] = Field(default_factory=list, description="Per-field error details")
