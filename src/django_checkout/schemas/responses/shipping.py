# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from pydantic import BaseModel, Field


class ShippingMethodResponse(BaseModel):
    """Available shipping method for GET shipping-methods/ endpoint."""

    code: str = Field(description="Shipping method code", examples=["std_delivery"])
    name: str | None = Field(None, description="Translatable name", examples=["Standard Delivery"])
    description: str | None = Field(None, description="Translatable description")
    image: str | None = Field(None, description="Method image URL")
    method_type: str = Field(description="default|inpost|pickup_point|delivery|virtual", examples=["default"])
    position: int = Field(0, description="Sort order")
    country_code: str | None = Field(None, description="Country for this option", examples=["PL"])
    currency: str | None = Field(None, description="Currency code", examples=["EUR"])

    gross: str | None = Field(None, description="Base shipping cost gross", examples=["19.99"])
    net: str | None = Field(None, description="Base shipping cost net", examples=["16.26"])
    modifier_gross: str | None = Field(None, description="Surcharge gross")
    modifier_net: str | None = Field(None, description="Surcharge net")
    final_gross: str | None = Field(None, description="Total cost gross (0.00 if free)", examples=["24.99"])
    final_net: str | None = Field(None, description="Total cost net", examples=["20.33"])
    tax_rate: str | None = Field(None, description="Tax rate decimal string", examples=["0.23"])

    is_eligible_for_free_shipping: bool = Field(False, description="Free shipping applies")
    amount_missing_for_free_shipping: str | None = Field(None, description="Amount to qualify")
    amount_required_for_free_shipping: str | None = Field(None, description="Free shipping threshold")

    cash_on_delivery_available: bool = Field(False, description="COD available for this method")
    cash_on_delivery_fee: str | None = Field(None, description="COD fee amount")


class PaymentMethodResponse(BaseModel):
    """Available payment method for GET payment-methods/ endpoint."""

    code: str = Field(description="Payment method code", examples=["payu"])
    provider: str = Field(description="Payment provider", examples=["payu"])
    name: str | None = Field(None, description="Translatable name", examples=["PayU"])
    description: str | None = Field(None, description="Translatable description")
    image: str | None = Field(None, description="Method image URL")
    position: int = Field(0, description="Sort order")
    is_cash_on_delivery: bool = Field(False, description="Whether this is COD payment")

    fee_display: str | None = Field(None, description="Formatted fee for display", examples=["2.5 %"])
    fee_gross: str | None = Field(None, description="Computed fee for current cart", examples=["2.50"])
    fee_net: str | None = Field(None, description="Computed fee net")
    fee_tax_rate: str | None = Field(None, description="Fee tax rate", examples=["0.23"])

    extension: dict | None = Field(None, description="Provider-specific data (e.g., banks list)")
