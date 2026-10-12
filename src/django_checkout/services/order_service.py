# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Order service — wraps order creation, listing, and detail for v2 API views."""

import logging
import uuid as uuid_mod
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q

from django_checkout.domain.cart import process_cart
from django_checkout.domain.validators.order import OrderValidation
from django_checkout.models import Cart, Order, OrderStatusLabel, PaymentMethod
from django_checkout.schemas.common import format_money
from django_checkout.utils import payment_entries
from django_checkout.worker.split_order.split_orders import split_orders_by_attribute
from django_checkout.worker.split_order.validator import check_that_order_can_be_split

logger = logging.getLogger("checkout.order_service")


@dataclass
class OrderCreateResult:
    order: Order
    redirect_url: str | None
    payment_failed: bool
    split_pretty_ids: list[str]


def create_order(
    channel,
    cart_id: str,
    customer=None,
    geo_country: str | None = None,
    request=None,
) -> OrderCreateResult:
    """Create order from cart. Validates ownership, reserves stock, initiates payment."""
    cart = Cart.objects.get(channel=channel, cart_id=cart_id)
    # SECURITY: validate cart ownership — prevent cross-customer order creation
    if cart.customer is not None and cart.customer != customer:
        raise PermissionError("Cart belongs to another customer")

    processed_data, messages = process_cart(
        channel,
        cart.as_data,
        customer=customer,
        geo_country=geo_country,
        cart=cart,
        is_order_creation=True,
    )
    OrderValidation.validate(cart_id, processed_data)

    items_to_split, len_parts = check_that_order_can_be_split(cart, channel)
    order = None
    redirect = None
    payment_error = None
    split_pretty_ids = []

    if not len_parts or len_parts <= 1:
        order, redirect, payment_error = Order.create(
            processed_data,
            cart,
            channel,
            customer=customer,
            request=request,
        )

    if len_parts and len_parts > 1:
        with transaction.atomic():
            split_pretty_ids, (order, redirect_split, payment_error) = split_orders_by_attribute(
                processed_data,
                channel,
                items_to_split,
                len_parts,
                customer,
                request,
                original_order=order,
                original_cart=cart,
            )
            if redirect_split:
                redirect = redirect_split

    if payment_error:
        logger.warning("Payment error for order %s: %s", order.order_id if order else "N/A", payment_error)

    return OrderCreateResult(
        order=order,
        redirect_url=redirect,
        payment_failed=bool(payment_error),
        split_pretty_ids=split_pretty_ids,
    )


def order_lookup_q(uid: str) -> Q:
    """Build Q filter for order lookup by pretty_id or UUID."""
    try:
        uuid_mod.UUID(uid)
        return Q(pretty_id_snap=uid) | Q(order_id=uid)
    except ValueError:
        return Q(pretty_id_snap=uid)


def _build_customer_order_queryset(channel, customer, params: dict):
    # SECURITY: customer=None would match every guest order in the channel. The view
    # already 401s, but this is a reusable unit now — it must not depend on that.
    if customer is None:
        return Order.objects.none()

    qs = (
        Order.objects.filter(channel=channel, customer=customer)
        .select_related(
            "customer__user",
        )
        .order_by("-created")
    )

    if status_filter := params.get("status"):
        qs = qs.filter(order_status=status_filter)
    if order_id_search := params.get("order_id"):
        # SECURITY: exact match only — prevent order ID enumeration via substring
        qs = qs.filter(order_lookup_q(order_id_search))

    ordering = params.get("ordering", "-created")
    allowed = {"created", "-created", "updated", "-updated"}
    if ordering in allowed:
        qs = qs.order_by(ordering)
    return qs


def _int_param(params: dict, name: str, default: int) -> int:
    try:
        return int(params.get(name, default))
    except (ValueError, TypeError):  # a junk page_size must not also reset a valid page
        return default


def _page_params(params: dict) -> tuple[int, int]:
    page = max(1, _int_param(params, "page", 1))
    page_size = min(max(1, _int_param(params, "page_size", 20)), 100)
    return page, page_size


def _payment_names(channel, language: str) -> dict[str, str | None]:
    """Localized payment-method names for the channel, by code — one query per request.

    Deliberately not ``Order.selected_payment_methods``: that reloads the DTO and queries
    per order, which would turn this list endpoint into an N+1.
    """
    return {
        code: (name_t9n or {}).get(language)
        for code, name_t9n in PaymentMethod.objects.filter(channel=channel).values_list("code", "name_t9n")
    }


def _amount(value) -> Decimal | None:
    """Money out of order_body, which is a free-form legacy blob.

    Never let a malformed amount raise — that is the same "unexpected shape reaches the
    serializer and 500s the whole page" failure this endpoint was fixed for.
    """
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        logger.warning("Unparseable amount in order_body: %r", value)
        return None


def _money(value) -> str | None:
    amount = _amount(value)
    return None if amount is None else format_money(amount)


def _total_net(body: dict) -> str | None:
    total, tax = _amount(body.get("total")), _amount(body.get("total_tax"))
    if total is None:
        return None
    return format_money(total - (tax or 0))


def _status_label(order, labels: dict, language: str) -> str:
    # name_t9n is nullable, and `language` is now the channel default rather than a
    # hardcoded "en" — so neither lookup is guaranteed to land.
    label_t9n = labels.get((order.order_status, order.channel_id)) or {}
    return label_t9n.get(language) or label_t9n.get("en") or order.order_status


def _payment_methods(body: dict, pm_names: dict) -> list[dict]:
    return [
        # fall back to the name stored at order time when the channel row is gone
        {"code": entry["code"], "name": pm_names.get(entry["code"]) or entry.get("name")}
        for entry in payment_entries(body)
        if entry.get("code")  # a codeless entry carries no information
    ]


def _serialize_order(order, labels, pm_names: dict, language: str) -> dict:
    """Serialize a single order for the customer list response."""
    body = order.order_body or {}
    return {
        "order_id": str(order.order_id),
        "pretty_id": order.pretty_id or "",
        "status": order.order_status,
        "status_label": _status_label(order, labels, language),
        "created": order.created,
        "updated": order.updated,
        "total_gross": _money(body.get("total")),
        "total_net": _total_net(body),
        "total_tax": _money(body.get("total_tax")),
        "currency": body.get("currency_code"),
        "country_code": body.get("country_code"),
        "item_count": len(body.get("cart", {}).get("items", [])),
        "shipping_method_code": (body.get("shipping_method") or {}).get("code"),
        "payment_methods": _payment_methods(body, pm_names),
        "attachments": [{"file_id": a.pk, "name": a.name} for a in order.orderattachment_set.all()],
    }


def list_customer_orders(channel, customer, params: dict) -> tuple[list[dict], int, str | None, str | None]:
    """List orders for customer with filters and pagination."""
    qs = _build_customer_order_queryset(channel, customer, params)
    total = qs.count()
    page, page_size = _page_params(params)
    offset = (page - 1) * page_size
    orders = qs.prefetch_related("orderattachment_set")[offset : offset + page_size]

    language = params.get("language") or channel.default_language.iso2
    labels = {(sl.status, sl.channel_id): sl.name_t9n for sl in OrderStatusLabel.objects.filter(channel=channel)}
    pm_names = _payment_names(channel, language)
    results = [_serialize_order(order, labels, pm_names, language) for order in orders]

    next_url = f"?page={page + 1}&page_size={page_size}" if offset + page_size < total else None
    prev_url = f"?page={page - 1}&page_size={page_size}" if page > 1 else None

    return results, total, next_url, prev_url
