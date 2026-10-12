# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from pydantic import BaseModel, Field, field_validator

# Opaque to checkout, but persisted verbatim into cart_body/order_body and echoed on
# every GET — bound it so an anonymous caller can't write an unbounded blob.
_VOUCHER_GIFT_MAX_KEYS = 12
_VOUCHER_GIFT_MAX_VALUE_LEN = 512
_VOUCHER_GIFT_MAX_TOTAL_LEN = 2048


class ItemInput(BaseModel):
    sku: str = Field(description="Product SKU", examples=["ENT-S004"])
    quantity: int = Field(ge=0, description="Quantity", examples=[2])
    offer_price: float | None = Field(None, description="Per-item price override (optional)")
    extra: dict | None = Field(None, description="Custom product options (optional)")
    voucher_gift: dict | None = Field(
        None, description="Gift-card personalization + design choice (opaque here; the voucher module validates it)"
    )

    @field_validator("voucher_gift")
    @classmethod
    def _bound_voucher_gift(cls, value: dict | None) -> dict | None:
        """Reject oversized / nested / key-flooded gift blobs (opaque but bounded)."""
        if value is None:
            return None
        if len(value) > _VOUCHER_GIFT_MAX_KEYS:
            raise ValueError(f"voucher_gift accepts at most {_VOUCHER_GIFT_MAX_KEYS} keys")
        total = 0
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("voucher_gift keys must be strings")
            if item is None:
                continue
            if isinstance(item, (dict, list)):
                raise ValueError("voucher_gift values must be scalars (no nesting)")
            text = str(item)
            if len(text) > _VOUCHER_GIFT_MAX_VALUE_LEN:
                raise ValueError(f"voucher_gift['{key}'] exceeds {_VOUCHER_GIFT_MAX_VALUE_LEN} chars")
            total += len(key) + len(text)
        if total > _VOUCHER_GIFT_MAX_TOTAL_LEN:
            raise ValueError(f"voucher_gift exceeds {_VOUCHER_GIFT_MAX_TOTAL_LEN} chars total")
        return value


class AddressInput(BaseModel):
    firstname: str = Field(description="First name", examples=["Jan"])
    lastname: str = Field(description="Last name", examples=["Kowalski"])
    street: str = Field(description="Street address", examples=["Marszalkowska 89"])
    city: str = Field(description="City", examples=["Warszawa"])
    postcode: str = Field(description="Postal code", examples=["00-001"])
    country_code: str = Field(min_length=2, max_length=2, description="ISO 2-letter country code", examples=["PL"])
    telephone: str = Field(description="Phone number", examples=["500500500"])
    dialling_code: str = Field(description="International dialling code", examples=["+48"])
    company: str | None = Field(None, description="Company name")
    is_company: bool = Field(False, description="Company address flag")
    tax_id: str | None = Field(None, description="Tax ID")
    address_id: int | None = Field(None, description="Accounts address PK (passthrough)")
    external_id: str | None = Field(None, description="External system ID")
    source: str | None = Field(None, description="'address_book' or 'manual'")


class BillingAddressInput(AddressInput):
    email: str = Field(description="Billing email", examples=["jan@example.com"])
    requested_invoice: bool = Field(False, description="Request invoice")
    vat_validation_status: bool | None = Field(None, description="VAT validation result")


class DiscountInput(BaseModel):
    code: str = Field(description="Discount code", examples=["SUMMER20"])
    sku: str | None = Field(None, description="Gratis product SKU (gratis modifiers only)")
    quantity: int | None = Field(None, description="Gratis quantity (gratis modifiers only)")


class DeliveryPointInput(BaseModel):
    code: str = Field(description="Delivery point code", examples=["WAW123"])
    name: str | None = Field(None, description="Point name")
    address: str | None = Field(None, description="Point address")
    city: str | None = Field(None, description="Point city")
    postcode: str | None = Field(None, description="Point postal code")


# --- Endpoint-specific request bodies ---


class CartCreateRequest(BaseModel):
    """POST carts/ — create a new cart."""

    items: list[ItemInput] = Field(description="Initial cart items")
    currency_code: str = Field(min_length=3, max_length=3, description="Currency ISO code", examples=["EUR"])
    language_code: str | None = Field(None, min_length=2, max_length=2, description="Language ISO2", examples=["en"])
    country_code: str | None = Field(None, min_length=2, max_length=2, description="Country ISO2", examples=["PL"])


class ItemsPatchRequest(BaseModel):
    """PATCH carts/{id}/items/ — update cart items."""

    items: list[ItemInput] = Field(description="Full item list (replaces existing)")


class AddressesPatchRequest(BaseModel):
    """PATCH carts/{id}/addresses/ — set billing and/or shipping address."""

    billing_address: BillingAddressInput | None = None
    shipping_address: AddressInput | None = Field(None, description="Null = same as billing")


class DiscountsPatchRequest(BaseModel):
    """PATCH carts/{id}/discounts/ — apply or remove discount codes."""

    codes: list[DiscountInput] = Field(default_factory=list, description="Discount codes to apply")
    clear: bool = Field(False, description="Remove all manual codes (automatic rules stay)")


class ShippingPatchRequest(BaseModel):
    """PATCH carts/{id}/shipping/ — select shipping method."""

    code: str = Field(description="Shipping method code", examples=["std_delivery"])
    delivery_point: DeliveryPointInput | None = Field(None, description="Pickup point (for inpost/pickup_point)")


class PaymentPatchRequest(BaseModel):
    """PATCH carts/{id}/payment/ — select payment method."""

    code: str = Field(description="Payment method code", examples=["payu"])
    bank_id: int | None = Field(None, description="Bank ID (Przelewy24)")
    card: str | None = Field(None, description="Saved card token")
    save_card: bool = Field(False, description="Save card for future use")
    pay_code: str | None = Field(
        None,
        max_length=256,
        description="Voucher code(s) to apply via this payment method, format 'CODE:PIN,CODE2' (PIN optional per code).",
        examples=["26WT1234:1234"],
    )


class CartMergeRequest(BaseModel):
    """POST carts/{id}/merge/ — merge guest cart into customer cart."""

    guest_cart_id: str = Field(description="Guest cart UUID to merge from")
    operation: str = Field("add", description="'add' (sum qty) or 'override' (replace)")
