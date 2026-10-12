# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Cart service — wraps domain process_cart() for v2 API views.

Each method follows the same pattern:
1. Load existing cart from DB (if updating)
2. Load CheckoutData from cart.cart_body
3. Build CartRequest merging v2 patch data with existing data
4. Call process_cart()
5. Save updated cart
6. Return (cart_record, domain_messages)
"""

import logging
from decimal import Decimal
from functools import reduce

from marshmallow import EXCLUDE

from django_checkout.domain.cart import process_cart
from django_checkout.domain.dto.address import Address, AddressData, BillingAddress
from django_checkout.domain.dto.cart import CartData, CartRequest, CheckoutData
from django_checkout.domain.dto.discount import DiscountData
from django_checkout.domain.dto.item import ItemData
from django_checkout.domain.dto.payment import PaymentData
from django_checkout.domain.dto.shipping import ShippingData
from django_checkout.enums import CartStatus
from django_checkout.models import Cart, Channel

logger = logging.getLogger("checkout.cart_service")


def create_cart(
    channel: Channel,
    items: list[dict],
    currency_code: str,
    language_code: str | None = None,
    country_code: str | None = None,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Create a new cart with initial items."""
    cart_data = CartData.factory()
    for item_dict in items:
        cart_data.items.append(
            ItemData(
                sku=item_dict["sku"],
                quantity=item_dict["quantity"],
                offer_price=item_dict.get("offer_price"),
                extra=item_dict.get("extra"),
                sub_items=None,
                voucher_gift=item_dict.get("voucher_gift"),
            )
        )

    body = CartRequest(
        cart=cart_data,
        addresses=None,
        payment_method=None,
        shipping_method=None,
        language_code=language_code,
        currency_code=currency_code,
        country_code=country_code,
        custom_order_id=None,
        requested_delivery_date=None,
        split_order=None,
        split_by_feature_idx=None,
        split_by_attr_idx=None,
        original_order_id=None,
        comment=None,
        affiliate_code=None,
        customer_ip=None,
    )

    processed_data, messages = process_cart(channel, body, customer=customer, geo_country=geo_country)
    record = Cart.create(processed_data, channel, customer=customer)
    record.save()
    return record, messages


def get_cart(channel: Channel, cart_id: str) -> Cart:
    """Load cart by cart_id and channel. Raises Cart.DoesNotExist if not found."""
    return Cart.objects.get(cart_id=cart_id, channel=channel, cart_status=CartStatus.NEW)


def get_latest_cart(channel: Channel, customer) -> Cart | None:
    """Get most recent active cart for customer."""
    if customer is None:
        return None
    return (
        Cart.objects.filter(channel=channel, customer=customer, cart_status=CartStatus.NEW)
        .filter(order__isnull=True)
        .order_by("-created")
        .first()
    )


def _build_address_from_dict(raw: dict | None, is_billing: bool = False):
    """Construct domain Address/BillingAddress from raw JSON dict."""
    if not raw:
        return None
    kwargs = {
        "email": raw.get("email", ""),
        "firstname": raw.get("firstname", ""),
        "lastname": raw.get("lastname", ""),
        "country_code": raw.get("country_code", ""),
        "city": raw.get("city", ""),
        "postcode": raw.get("postcode", ""),
        "street": raw.get("street", ""),
        "dialling_code": raw.get("dialling_code", ""),
        "telephone": raw.get("telephone", ""),
        "company": raw.get("company"),
        "address_id": raw.get("address_id"),
        "external_id": raw.get("external_id"),
    }
    # Only billing addresses carry invoice/VAT fields. Do NOT promote a shipping address
    # to BillingAddress just because the raw dict has a `tax_id` key — the v2 AddressInput
    # schema always includes `tax_id: None`, which previously stored shipping addresses in a
    # billing shape that the `shipping_address` DTO union (SimplifiedAddress | Address) could
    # not reload, breaking `Cart.as_data`.
    if is_billing:
        return BillingAddress(
            **kwargs,
            requested_invoice=raw.get("requested_invoice", False),
            vat_validation_status=raw.get("vat_validation_status", False),
            invoice_requested_status=raw.get("invoice_requested_status", False),
            tax_id=raw.get("tax_id"),
        )
    return Address(**kwargs)


def _build_items_from_list(raw_items: list | None) -> list:
    """Construct domain ItemData list from raw JSON, preserving validated fields."""
    if not raw_items:
        return []
    items = []
    for raw in raw_items:
        items.append(
            ItemData(
                sku=raw.get("sku", ""),
                quantity=Decimal(str(raw.get("quantity", 0))),
                offer_price=Decimal(str(raw["offer_price"])) if raw.get("offer_price") else None,
                extra=raw.get("extra"),
                sub_items=None,
                voucher_gift=raw.get("voucher_gift"),
            )
        )
    return items


def _load_checkout_data(record: Cart) -> CheckoutData:
    """Deserialize CheckoutData from cart_body JSON.

    Marshmallow Union types (SimplifiedAddress|BillingAddress, ItemData|ValidatedItemData)
    fail when the stored data has more fields than the narrower type expects, because nested
    schemas use unknown=RAISE. We try marshmallow first, then fall back to manual construction
    from the raw dict — preserving all data.
    """
    try:
        return CheckoutData.Schema(unknown=EXCLUDE).load(record.cart_body)
    except Exception as exc:
        logger.debug("Marshmallow deserialization failed for cart %s, falling back to manual: %s", record.cart_id, exc)

    # Marshmallow failed. Build CheckoutData from raw dict directly.
    raw = record.cart_body or {}
    raw_cart = raw.get("cart") or {}

    from django_checkout.domain.dto.address import AddressData
    from django_checkout.domain.dto.discount import DiscountData

    # Reconstruct items as ItemData (process_cart will re-validate and produce ValidatedItemData)
    items = _build_items_from_list(raw_cart.get("items"))

    # Reconstruct discounts
    discounts = []
    for d in raw_cart.get("discounts") or []:
        discounts.append(
            DiscountData(
                code=d.get("code", ""),
                sku=d.get("sku"),
                quantity=d.get("quantity"),
                clear_discounts=d.get("clear_discounts", False),
            )
        )

    cart_data = CartData(
        items=items,
        discount_amount=Decimal(str(raw_cart["discount_amount"])) if raw_cart.get("discount_amount") else None,
        discounts=discounts,
        base_total_price=Decimal(str(raw_cart["base_total_price"])) if raw_cart.get("base_total_price") else None,
        base_netto_price=Decimal(str(raw_cart["base_netto_price"])) if raw_cart.get("base_netto_price") else None,
        total_price=Decimal(str(raw_cart["total_price"])) if raw_cart.get("total_price") else None,
        validation_status=raw_cart.get("validation_status"),
    )

    # Reconstruct addresses
    raw_addrs = raw.get("addresses")
    addresses = None
    if raw_addrs:
        addresses = AddressData(
            billing_address=_build_address_from_dict(raw_addrs.get("billing_address"), is_billing=True),
            shipping_address=_build_address_from_dict(raw_addrs.get("shipping_address")),
            validation_status=raw_addrs.get("validation_status"),
        )

    # Reconstruct shipping/payment as None — process_cart will re-validate from the CartRequest
    data = CheckoutData(
        cart=cart_data,
        addresses=addresses,
        payment_method=None,
        shipping_method=None,
        total_to_min_order_price=raw.get("total_to_min_order_price"),
        base_total=Decimal(str(raw.get("base_total", 0))),
        total=Decimal(str(raw.get("total", 0))),
        fee_price=Decimal(str(raw.get("fee_price", 0))),
        fee_tax_price=Decimal(str(raw.get("fee_tax_price", 0))),
        fee_tax_rate=Decimal(str(raw.get("fee_tax_rate", 0))),
        total_tax=Decimal(str(raw.get("total_tax", 0))),
        language_code=raw.get("language_code"),
        currency_code=raw.get("currency_code"),
        country_code=raw.get("country_code"),
        requested_delivery_date=raw.get("requested_delivery_date"),
        need_full_address=raw.get("need_full_address", True),
        custom_order_id=raw.get("custom_order_id"),
        split_order=raw.get("split_order", False),
        split_by_feature_idx=raw.get("split_by_feature_idx"),
        split_by_attr_idx=raw.get("split_by_attr_idx"),
        original_order_id=raw.get("original_order_id"),
        comment=raw.get("comment"),
        affiliate_code=raw.get("affiliate_code"),
        customer_ip=raw.get("customer_ip"),
        validation_status=raw.get("validation_status", "invalid"),
    )
    return data


def _process_and_save(
    channel: Channel,
    body: CartRequest,
    record: Cart,
    checkout_data: CheckoutData,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Run process_cart, save, return record + messages."""
    processed_data, messages = process_cart(
        channel, body, checkout_data, customer=customer, geo_country=geo_country, cart=record
    )
    record = record.replace(processed_data)
    record.save()
    return record, messages


def refresh_cart_body(cart: Cart) -> Cart:
    """Re-run `process_cart` on a persisted cart to refresh `cart_body` totals.

    Public hook for modules that mutate cart-affecting state out-of-band
    (e.g. ``django-checkout-voucher`` adding/removing ``CartVoucher`` rows)
    and need stored ``cart_body`` to reflect the new totals on the next GET
    without forcing the storefront to issue a PUT.

    Idempotent: calling it twice does the same work twice; concurrent callers
    are guarded by ``Cart.save()`` last-writer-wins semantics. Domain-specific
    re-entrancy guards (e.g. avoiding recursion when the caller is itself
    inside ``process_cart``) are the caller's responsibility.

    Raises whatever ``process_cart`` raises — do NOT catch broadly here.
    """
    checkout_data = _load_checkout_data(cart)
    body = _rebuild_cart_request(checkout_data)
    refreshed, _messages = _process_and_save(channel=cart.channel, body=body, record=cart, checkout_data=checkout_data)
    return refreshed


def recompute_checkout_data(cart: Cart) -> CheckoutData:
    """Re-run ``process_cart`` on a persisted cart and return fresh data WITHOUT saving.

    Read-only counterpart to :func:`refresh_cart_body`, for GET endpoints that
    need automatic discount rules re-discovered (they are only applied inside
    ``process_cart``, i.e. the write path) without turning a read into a DB
    write. This path is side-effect-free: automatic-discount discovery and the
    price/quantity fetches only SELECT — no stock reservation, no
    ``DiscountCode.current_used`` bumps, no order/email side effects (those are
    gated behind ``is_order_creation``/``declarative_payment_method``, which
    this call does not set).

    Raises whatever ``process_cart`` raises — the caller decides on fallback.
    """
    checkout_data = _load_checkout_data(cart)
    body = _rebuild_cart_request(checkout_data)
    processed_data, _messages = process_cart(cart.channel, body, checkout_data, cart=cart)
    return processed_data


def _rebuild_cart_request(checkout_data: CheckoutData, **overrides) -> CartRequest:
    """Build CartRequest from existing CheckoutData, overriding specific fields."""
    return CartRequest(
        cart=overrides.get("cart", checkout_data.cart),
        addresses=overrides.get("addresses", checkout_data.addresses),
        payment_method=overrides.get("payment_method", checkout_data.payment_method),
        shipping_method=overrides.get("shipping_method", checkout_data.shipping_method),
        language_code=checkout_data.language_code,
        currency_code=checkout_data.currency_code,
        country_code=checkout_data.country_code,
        custom_order_id=checkout_data.custom_order_id,
        requested_delivery_date=checkout_data.requested_delivery_date,
        split_order=checkout_data.split_order,
        split_by_feature_idx=checkout_data.split_by_feature_idx,
        split_by_attr_idx=checkout_data.split_by_attr_idx,
        original_order_id=checkout_data.original_order_id,
        comment=checkout_data.comment,
        affiliate_code=checkout_data.affiliate_code,
        customer_ip=checkout_data.customer_ip,
    )


def patch_items(
    channel: Channel,
    cart_id: str,
    items: list[dict],
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Update cart items. Other sections preserved from existing data."""
    record = get_cart(channel, cart_id)
    checkout_data = _load_checkout_data(record)

    cart_data = CartData.factory()
    for item_dict in items:
        cart_data.items.append(
            ItemData(
                sku=item_dict["sku"],
                quantity=item_dict["quantity"],
                offer_price=item_dict.get("offer_price"),
                extra=item_dict.get("extra"),
                sub_items=None,
                voucher_gift=item_dict.get("voucher_gift"),
            )
        )
    if checkout_data.cart and checkout_data.cart.discounts:
        cart_data.discounts = checkout_data.cart.discounts

    body = _rebuild_cart_request(checkout_data, cart=cart_data)
    return _process_and_save(channel, body, record, checkout_data, customer, geo_country)


def patch_addresses(
    channel: Channel,
    cart_id: str,
    billing: dict | None,
    shipping: dict | None,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Set billing and/or shipping address. May cascade: reset shipping/payment if country changes."""
    record = get_cart(channel, cart_id)
    checkout_data = _load_checkout_data(record)

    addresses = AddressData(
        billing_address=_build_address_from_dict(billing, is_billing=True),
        shipping_address=_build_address_from_dict(shipping),
        validation_status=None,
    )

    body = _rebuild_cart_request(checkout_data, addresses=addresses)
    return _process_and_save(channel, body, record, checkout_data, customer, geo_country)


def patch_discounts(
    channel: Channel,
    cart_id: str,
    codes: list[dict],
    clear: bool = False,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Apply or remove discount codes. Automatic rules are always re-discovered."""
    record = get_cart(channel, cart_id)
    checkout_data = _load_checkout_data(record)

    discounts = []
    if clear:
        discounts.append(DiscountData(code="", sku=None, quantity=None, clear_discounts=True))
    for code_dict in codes:
        discounts.append(
            DiscountData(
                code=code_dict["code"],
                sku=code_dict.get("sku"),
                quantity=code_dict.get("quantity"),
                clear_discounts=False,
            )
        )

    cart_data = checkout_data.cart
    cart_data.discounts = discounts

    body = _rebuild_cart_request(checkout_data, cart=cart_data)
    return _process_and_save(channel, body, record, checkout_data, customer, geo_country)


def patch_shipping(
    channel: Channel,
    cart_id: str,
    code: str,
    delivery_point: dict | None = None,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Select shipping method."""
    record = get_cart(channel, cart_id)
    checkout_data = _load_checkout_data(record)

    shipping = ShippingData(code=code, name=None, country_code=None, delivery_point=delivery_point)

    body = _rebuild_cart_request(checkout_data, shipping_method=shipping)
    return _process_and_save(channel, body, record, checkout_data, customer, geo_country)


def patch_payment(
    channel: Channel,
    cart_id: str,
    code: str,
    bank_id: int | None = None,
    card: str | None = None,
    save_card: bool = False,
    pay_code: str | None = None,
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Select payment method."""
    record = get_cart(channel, cart_id)
    checkout_data = _load_checkout_data(record)

    payment = PaymentData(
        code=code,
        name=None,
        card=card,
        bank_id=bank_id,
        country_code=None,
        authorization_token=None,
        continue_url=None,
        save_card=save_card,
        pay_code=pay_code,
    )

    body = _rebuild_cart_request(checkout_data, payment_method=payment)
    return _process_and_save(channel, body, record, checkout_data, customer, geo_country)


def close_cart(channel: Channel, cart_id: str) -> Cart:
    """Close cart (set status to CLOSED)."""
    record = get_cart(channel, cart_id)
    record.cart_status = CartStatus.CLOSED
    record.save()
    return record


def merge_carts(
    channel: Channel,
    target_cart_id: str,
    guest_cart_id: str,
    operation: str = "add",
    customer=None,
    geo_country: str | None = None,
) -> tuple[Cart, list]:
    """Merge guest cart into customer's cart. Guest cart is deleted after merge."""
    customer_record = get_cart(channel, target_cart_id)
    # SECURITY: validate target cart belongs to the authenticated customer
    if customer and customer_record.customer is not None and customer_record.customer != customer:
        raise Cart.DoesNotExist("Cart does not belong to this customer")
    guest_record = Cart.objects.filter(cart_id=guest_cart_id, channel=channel, customer=None).first()
    if guest_record is None:
        raise Cart.DoesNotExist("Guest cart not found")

    checkout_data_customer = _load_checkout_data(customer_record)
    checkout_data_guest = _load_checkout_data(guest_record)

    items_customer = [(item.sku, item.quantity) for item in (checkout_data_customer.cart.items or [])]
    items_guest = [(item.sku, item.quantity) for item in (checkout_data_guest.cart.items or [])]

    if operation == "override":
        merged = items_guest
    else:
        merged = items_customer + items_guest

    unique_skus = reduce(lambda res, tpl: dict(res, **{tpl[0]: res.get(tpl[0], 0) + tpl[1]}), merged, {})

    # Preserve per-line payloads (extra, voucher_gift) across the merge, else a guest's
    # gift personalization is lost on login. Guest items come last, so guest wins.
    payloads: dict[str, tuple[dict | None, dict | None]] = {}
    ordered = list(checkout_data_customer.cart.items or []) + list(checkout_data_guest.cart.items or [])
    if operation == "override":
        ordered = list(checkout_data_guest.cart.items or [])
    for item in ordered:
        prev_extra, prev_gift = payloads.get(item.sku, (None, None))
        extra = getattr(item, "extra", None)
        gift = getattr(item, "voucher_gift", None)
        payloads[item.sku] = (extra if extra is not None else prev_extra, gift if gift is not None else prev_gift)

    body = CartRequest.factory()
    for sku, qty in unique_skus.items():
        extra, gift = payloads.get(sku, (None, None))
        body.cart.items.append(
            ItemData(sku=sku, quantity=qty, offer_price=None, extra=extra, sub_items=None, voucher_gift=gift)
        )

    processed_data, messages = process_cart(
        channel, body, checkout_data_customer, customer=customer, geo_country=geo_country
    )
    record = customer_record.replace(processed_data)
    guest_record.delete()
    record.save()
    return record, messages
