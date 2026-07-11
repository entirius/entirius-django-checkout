# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from django_checkout.schemas.common import format_money, format_tax_rate
from django_checkout.schemas.responses.address import AddressesResponse


class ItemResponse(BaseModel):
    """v2 cart item — explicit gross/net price fields aligned with Matrix v2."""

    sku: str = Field(description="Product SKU", examples=["ENT-S004"])
    quantity: int = Field(description="Item quantity", examples=[2])
    status: str = Field(description="Item validation status", examples=["valid"])
    is_gratis: bool = Field(False, description="Whether item is a free gift")

    # Catalog prices (before discounts)
    unit_gross: str | None = Field(None, description="Catalog price per unit gross", examples=["249.99"])
    unit_net: str | None = Field(None, description="Catalog price per unit net", examples=["203.24"])
    total_gross: str | None = Field(None, description="Catalog price x qty gross", examples=["499.98"])
    total_net: str | None = Field(None, description="Catalog price x qty net", examples=["406.49"])

    # Special prices (promo/sale)
    has_special_price: bool = Field(False, description="Whether a special price applies")
    special_unit_gross: str | None = Field(None, description="Promo price per unit gross", examples=["199.99"])
    special_unit_net: str | None = Field(None, description="Promo price per unit net", examples=["162.60"])
    special_total_gross: str | None = Field(None, description="Promo price x qty gross", examples=["399.98"])
    special_total_net: str | None = Field(None, description="Promo price x qty net", examples=["325.19"])
    percent_off: int | None = Field(None, description="Special vs catalog discount %", examples=[20])

    # Final prices (after all discounts)
    final_unit_gross: str | None = Field(None, description="Final price per unit gross", examples=["199.99"])
    final_unit_net: str | None = Field(None, description="Final price per unit net", examples=["162.60"])
    final_total_gross: str | None = Field(None, description="Final price x qty gross", examples=["399.98"])
    final_total_net: str | None = Field(None, description="Final price x qty net", examples=["325.19"])

    # Tax
    unit_tax: str | None = Field(None, description="Tax amount per unit", examples=["37.39"])
    total_tax: str | None = Field(None, description="Tax amount x qty", examples=["74.79"])
    tax_rate: str | None = Field(None, description="Tax rate as decimal string", examples=["0.23"])

    # Discounts
    discount_gross: str | None = Field(None, description="Discount amount gross", examples=["100.00"])
    discount_net: str | None = Field(None, description="Discount amount net", examples=["81.30"])

    @classmethod
    def from_domain(cls, item) -> ItemResponse:
        """Translate ValidatedItemData to v2 response. Core field rename layer."""
        tax_rate_decimal = Decimal(str(item.tax_rate)) if item.tax_rate else Decimal("0")
        tax_divisor = Decimal("1") + (tax_rate_decimal if tax_rate_decimal < 1 else tax_rate_decimal / Decimal("100"))

        def compute_net(gross_val: Decimal | None) -> str | None:
            if gross_val is None:
                return None
            return format_money(Decimal(str(gross_val)) / tax_divisor)

        base_unit = Decimal(str(item.base_unit_price)) if item.base_unit_price else None
        base_total = Decimal(str(item.base_total_price)) if item.base_total_price else None
        special_unit = Decimal(str(item.special_unit_price)) if item.special_unit_price else None
        special_total = Decimal(str(item.special_total_price)) if item.special_total_price else None
        qty = int(item.quantity) if item.quantity else 0

        return cls(
            sku=item.sku,
            quantity=qty,
            status=item.status.value if hasattr(item.status, "value") else str(item.status),
            is_gratis=getattr(item, "is_gratis", False),
            # Catalog prices
            unit_gross=format_money(base_unit),
            unit_net=compute_net(base_unit) if not getattr(item, "unit_price_netto", None) else None,
            total_gross=format_money(base_total),
            total_net=compute_net(base_total),
            # Special prices
            has_special_price=special_unit is not None and special_unit > 0,
            special_unit_gross=format_money(special_unit),
            special_unit_net=compute_net(special_unit),
            special_total_gross=format_money(special_total),
            special_total_net=compute_net(special_total),
            percent_off=getattr(item, "special_percent", None),
            # Final prices (after discounts)
            final_unit_gross=format_money(item.unit_price),
            final_unit_net=format_money(item.unit_price_netto)
            if item.unit_price_netto
            else compute_net(Decimal(str(item.unit_price)) if item.unit_price else None),
            final_total_gross=format_money(item.total_price),
            final_total_net=format_money(item.total_price_netto)
            if item.total_price_netto
            else compute_net(Decimal(str(item.total_price)) if item.total_price else None),
            # Tax
            unit_tax=format_money(item.unit_tax_amount),
            total_tax=format_money(item.total_tax_amount),
            tax_rate=format_tax_rate(item.tax_rate),
            # Discounts
            discount_gross=format_money(item.discount_amount),
            discount_net=format_money(item.discount_amount_netto)
            if item.discount_amount_netto
            else compute_net(Decimal(str(item.discount_amount)) if item.discount_amount else None),
        )


class DiscountResponse(BaseModel):
    """Applied discount in cart response."""

    code: str = Field(description="Discount code", examples=["SUMMER20"])
    modifier: str | None = Field(None, description="Discount type", examples=["percent_discount"])
    status: str = Field(description="Discount validation status", examples=["valid"])
    free_shipping: bool = Field(False, description="Grants free shipping")
    free_order: bool | None = Field(None, description="Makes order free")
    is_automatic: bool = Field(False, description="Server-discovered, not removable")
    gratis_sku: str | None = Field(None, description="Selected gratis product SKU")
    gratis_quantity: int | None = Field(None, description="Selected gratis quantity")

    @classmethod
    def from_domain(cls, discount) -> DiscountResponse:
        return cls(
            code=discount.code,
            modifier=getattr(discount, "modifier", None),
            status=discount.status.value if hasattr(discount.status, "value") else str(discount.status),
            free_shipping=getattr(discount, "free_shipping", False),
            free_order=getattr(discount, "free_order", None),
            is_automatic=getattr(discount, "is_automatic", False),
            gratis_sku=getattr(discount, "sku", None) if getattr(discount, "sku", "") else None,
            gratis_quantity=getattr(discount, "quantity", None) if getattr(discount, "quantity", 0) else None,
        )


class ShippingInCartResponse(BaseModel):
    """Selected shipping method in cart response."""

    code: str = Field(description="Shipping method code", examples=["std_delivery"])
    name: str | None = Field(None, description="Shipping method name")
    method_type: str | None = Field(None, description="default|inpost|pickup_point|delivery|virtual")
    status: str = Field(description="Validation status", examples=["valid"])

    gross: str | None = Field(None, description="Base shipping cost gross", examples=["19.99"])
    net: str | None = Field(None, description="Base shipping cost net", examples=["16.26"])
    modifier_gross: str | None = Field(None, description="Additional surcharge gross")
    modifier_net: str | None = Field(None, description="Additional surcharge net")
    final_gross: str | None = Field(None, description="Final shipping cost gross", examples=["19.99"])
    final_net: str | None = Field(None, description="Final shipping cost net", examples=["16.26"])
    tax: str | None = Field(None, description="Tax amount", examples=["3.73"])
    tax_rate: str | None = Field(None, description="Tax rate decimal string", examples=["0.23"])

    cash_on_delivery_fee: str | None = Field(None, description="COD fee if applicable")
    delivery_point: dict | None = Field(None, description="Selected delivery point data")

    @classmethod
    def from_domain(cls, shipping) -> ShippingInCartResponse | None:
        if shipping is None:
            return None
        tax_rate_dec = Decimal(str(shipping.tax_rate)) if shipping.tax_rate else Decimal("0")
        tax_divisor = Decimal("1") + (tax_rate_dec if tax_rate_dec < 1 else tax_rate_dec / Decimal("100"))

        def compute_net(gross_val):
            if gross_val is None:
                return None
            return format_money(Decimal(str(gross_val)) / tax_divisor)

        base_price = getattr(shipping, "normal_price", None) or getattr(shipping, "base_unit_price", None)
        modifier = getattr(shipping, "modifier_price", None)
        unit_price = Decimal(str(shipping.unit_price)) if shipping.unit_price else Decimal("0")

        dp = getattr(shipping, "delivery_point", None)
        delivery_point_dict = None
        if dp is not None:
            if isinstance(dp, dict):
                delivery_point_dict = dp
            elif hasattr(dp, "code"):
                delivery_point_dict = {
                    "code": getattr(dp, "code", None),
                    "name": getattr(dp, "name", None),
                    "address": getattr(dp, "address", None),
                    "city": getattr(dp, "city", None),
                    "postcode": getattr(dp, "postcode", None),
                }

        return cls(
            code=shipping.code,
            name=getattr(shipping, "name", None),
            method_type=None,
            status=shipping.status.value if hasattr(shipping.status, "value") else str(shipping.status),
            gross=format_money(base_price),
            net=compute_net(base_price),
            modifier_gross=format_money(modifier) if modifier else None,
            modifier_net=compute_net(modifier) if modifier else None,
            final_gross=format_money(unit_price),
            final_net=format_money(shipping.unit_price_netto) if shipping.unit_price_netto else compute_net(unit_price),
            tax=format_money(shipping.tax_amount) if getattr(shipping, "tax_amount", None) else None,
            tax_rate=format_tax_rate(shipping.tax_rate),
            cash_on_delivery_fee=format_money(shipping.cash_on_delivery_fee) if shipping.cash_on_delivery_fee else None,
            delivery_point=delivery_point_dict,
        )


class PaymentInCartResponse(BaseModel):
    """Selected payment method in cart response."""

    code: str = Field(description="Payment method code", examples=["payu"])
    name: str | None = Field(None, description="Payment method name")
    provider: str | None = Field(None, description="Payment provider", examples=["payu"])
    status: str = Field(description="Validation status", examples=["valid"])
    is_cash_on_delivery: bool = Field(False, description="Whether this is COD payment")

    fee_gross: str | None = Field(None, description="Payment fee gross")
    fee_net: str | None = Field(None, description="Payment fee net")
    fee_tax_rate: str | None = Field(None, description="Payment fee tax rate")

    bank_id: int | None = Field(None, description="Selected bank ID (Przelewy24)")
    # SECURITY: card token, authorization_token, continue_url are NOT exposed in v2 response.
    # They exist in cart_body/order_body (DB) but are redacted at the API layer.
    save_card: bool = Field(False, description="User opted to save card")
    pay_code: str | None = Field(None, description="Generated pay code")

    @classmethod
    def from_domain(cls, payment, fee_price=None, fee_tax_rate=None) -> PaymentInCartResponse | None:
        if payment is None:
            return None
        return cls(
            code=payment.code,
            name=getattr(payment, "name", None),
            provider=None,
            status=payment.status.value if hasattr(payment.status, "value") else str(payment.status),
            is_cash_on_delivery=getattr(payment, "is_cash_on_delivery", False) or False,
            fee_gross=format_money(fee_price) if fee_price else None,
            fee_net=None,
            fee_tax_rate=format_tax_rate(fee_tax_rate) if fee_tax_rate else None,
            bank_id=getattr(payment, "bank_id", None),
            save_card=getattr(payment, "save_card", False) or False,
            pay_code=getattr(payment, "pay_code", None),
        )


class GratisInfoResponse(BaseModel):
    """Gratis/free product summary in cart response."""

    is_available: bool = Field(False, description="Whether gratis rules apply")
    nearest_tier_price: str | None = Field(None, description="Price threshold for nearest gratis tier")
    amount_missing: str | None = Field(None, description="Amount missing for nearest tier")
    rules: list[dict] = Field(default_factory=list, description="Available gratis rules (always array)")


class CartV2Response(BaseModel):
    """Full v2 cart response — explicit gross/net, string amounts, decimal tax_rate."""

    cart_id: str = Field(description="Cart UUID", examples=["550e8400-e29b-41d4-a716-446655440000"])
    cart_status: str = Field(description="Cart status", examples=["NEW"])
    validation_status: str = Field(description="Overall validation", examples=["valid"])
    currency: str | None = Field(None, description="Currency code", examples=["EUR"])

    # Cart totals
    subtotal_gross: str = Field(description="Cart subtotal gross (before shipping)", examples=["399.98"])
    subtotal_net: str | None = Field(None, description="Cart subtotal net", examples=["325.19"])
    total_gross: str = Field(description="Cart total gross (incl shipping + fee)", examples=["419.97"])
    total_net: str | None = Field(None, description="Cart total net", examples=["341.44"])
    total_tax: str | None = Field(None, description="Total tax amount", examples=["78.53"])
    tax_rates: dict[str, str] | None = Field(None, description="Tax breakdown by rate")

    # Items
    items: list[ItemResponse] = Field(default_factory=list, description="Cart line items")

    # Addresses
    addresses: AddressesResponse | None = None

    # Shipping & payment
    shipping_method: ShippingInCartResponse | None = None
    payment_method: PaymentInCartResponse | None = None

    # Discounts
    discounts: list[DiscountResponse] = Field(default_factory=list, description="Applied discount codes")
    total_discount_gross: str | None = Field(None, description="Sum of all item discount_gross")
    total_discount_net: str | None = Field(None, description="Sum of all item discount_net")

    # Fee
    fee_gross: str | None = Field(None, description="Payment method fee gross")
    fee_net: str | None = Field(None, description="Payment method fee net")
    fee_tax: str | None = Field(None, description="Payment method fee tax")

    # Free shipping info
    free_shipping: bool = Field(False, description="Whether free shipping applies")
    amount_missing_for_free_shipping: str | None = Field(None, description="Amount to qualify for free shipping")
    amount_required_for_free_shipping: str | None = Field(None, description="Free shipping threshold")

    # Gratis
    gratis: GratisInfoResponse | None = None

    # Min order
    min_order_price: str | None = Field(None, description="Minimum order amount")
    amount_missing_for_min_order: str | None = Field(None, description="How much more needed for min order")

    # Cart capabilities
    can_be_split: bool = Field(False, description="Whether order will be split")
    allowed_guest_checkout: bool = Field(True, description="Guest checkout allowed")
    need_full_address: bool = Field(True, description="Full address required")

    @classmethod
    def from_domain(cls, checkout_data, cart_id: str, cart_status: str, **extra) -> CartV2Response:
        """
        Translate CheckoutData + CartResponse fields to v2 response.

        Args:
            checkout_data: CheckoutData or CartResponse domain dataclass
            cart_id: Cart UUID string
            cart_status: Cart status string
            **extra: Additional fields (free_shipping, can_be_split, tax_rates, min_order_price, etc.)
        """
        cart = checkout_data.cart
        items = []
        if cart and cart.items:
            for item in cart.items:
                if hasattr(item, "base_unit_price"):
                    items.append(ItemResponse.from_domain(item))
                else:
                    # ItemData (not yet validated) — include with minimal fields
                    items.append(
                        ItemResponse(
                            sku=item.sku,
                            quantity=int(item.quantity) if item.quantity else 0,
                            status="pending",
                            is_gratis=getattr(item, "is_gratis", False),
                        )
                    )

        discounts = []
        if cart and cart.discounts:
            for d in cart.discounts:
                if hasattr(d, "modifier"):
                    discounts.append(DiscountResponse.from_domain(d))

        # Compute net totals from gross
        base_total = Decimal(str(checkout_data.base_total)) if checkout_data.base_total else Decimal("0")
        total = Decimal(str(checkout_data.total)) if checkout_data.total else Decimal("0")
        total_tax = Decimal(str(checkout_data.total_tax)) if checkout_data.total_tax else Decimal("0")
        subtotal_net = base_total - total_tax if total_tax else None
        total_net = total - total_tax if total_tax else None

        # Discount totals
        discount_gross = cart.discount_amount if cart else None
        discount_net = cart.discount_amount_netto if cart else None

        # Fee
        fee_price = checkout_data.fee_price if checkout_data.fee_price else None
        fee_tax = checkout_data.fee_tax_price if getattr(checkout_data, "fee_tax_price", None) else None
        fee_tax_rate = checkout_data.fee_tax_rate if getattr(checkout_data, "fee_tax_rate", None) else None

        # Tax rates: convert int keys to decimal string keys
        raw_tax_rates = extra.get("tax_rates") or getattr(checkout_data, "tax_rates", None)
        tax_rates = None
        if raw_tax_rates and isinstance(raw_tax_rates, dict):
            tax_rates = {}
            for k, v in raw_tax_rates.items():
                if k == "total":
                    continue
                try:
                    tax_rates[format_tax_rate(k)] = format_money(v)
                except Exception:
                    tax_rates[str(k)] = format_money(v)

        # Gratis info
        gratis = None
        if cart:
            gratis = GratisInfoResponse(
                is_available=getattr(cart, "is_gratis_available", False) or False,
                nearest_tier_price=format_money(getattr(cart, "amount_required_for_nearest_gratis_rule", None)),
                amount_missing=format_money(getattr(cart, "amount_missing_for_nearest_gratis_rule", None)),
                rules=getattr(cart, "gratis_rules", None) or [],
            )

        return cls(
            cart_id=cart_id,
            cart_status=cart_status,
            validation_status=(
                checkout_data.validation_status.value
                if hasattr(checkout_data.validation_status, "value")
                else str(checkout_data.validation_status)
            ),
            currency=checkout_data.currency_code,
            subtotal_gross=format_money(base_total),
            subtotal_net=format_money(subtotal_net),
            total_gross=format_money(total),
            total_net=format_money(total_net),
            total_tax=format_money(checkout_data.total_tax),
            tax_rates=tax_rates,
            items=items,
            addresses=AddressesResponse.from_domain(checkout_data.addresses),
            shipping_method=ShippingInCartResponse.from_domain(checkout_data.shipping_method),
            payment_method=PaymentInCartResponse.from_domain(
                checkout_data.primary_payment_method, fee_price=fee_price, fee_tax_rate=fee_tax_rate
            ),
            discounts=discounts,
            total_discount_gross=format_money(discount_gross),
            total_discount_net=format_money(discount_net),
            fee_gross=format_money(fee_price) if fee_price else None,
            fee_net=None,
            fee_tax=format_money(fee_tax) if fee_tax else None,
            free_shipping=extra.get("free_shipping", False),
            amount_missing_for_free_shipping=format_money(extra.get("amount_missing_for_free_shipping")),
            amount_required_for_free_shipping=format_money(extra.get("amount_required_for_free_shipping")),
            gratis=gratis,
            min_order_price=format_money(extra.get("min_order_price")),
            amount_missing_for_min_order=format_money(
                checkout_data.total_to_min_order_price
                if getattr(checkout_data, "total_to_min_order_price", None)
                else None
            ),
            can_be_split=extra.get("can_be_split", False),
            allowed_guest_checkout=getattr(cart, "allowed_guest_checkout", True) or True,
            need_full_address=getattr(checkout_data, "need_full_address", True) or True,
        )
