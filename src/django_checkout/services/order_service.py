# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Order service — wraps order creation, listing, and detail for v2 API views."""

import logging
import uuid as uuid_mod
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q

from django_checkout.domain.cart import process_cart
from django_checkout.domain.validators.order import OrderValidation
from django_checkout.models import Cart, Order, OrderStatusLabel
from django_checkout.schemas.common import format_money
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


def list_customer_orders(channel, customer, params: dict) -> tuple[list[dict], int, str | None, str | None]:
    """List orders for customer with filters and pagination."""
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

    total = qs.count()

    try:
        page = max(1, int(params.get("page", 1)))
        page_size = min(max(1, int(params.get("page_size", 20))), 100)
    except (ValueError, TypeError):
        page, page_size = 1, 20

    offset = (page - 1) * page_size
    orders = qs.prefetch_related("orderattachment_set")[offset : offset + page_size]

    labels = {(sl.status, sl.channel_id): sl.name_t9n for sl in OrderStatusLabel.objects.filter(channel=channel)}
    language = params.get("language", "en")

    results = []
    for order in orders:
        label_t9n = labels.get((order.order_status, channel.pk), {})
        body = order.order_body or {}
        results.append(
            {
                "order_id": str(order.order_id),
                "pretty_id": order.pretty_id or "",
                "status": order.order_status,
                "status_label": label_t9n.get(language, label_t9n.get("en", order.order_status)),
                "created": order.created,
                "updated": order.updated,
                "total_gross": format_money(body.get("total")),
                "total_net": None,
                "currency": body.get("currency_code"),
                "item_count": len(body.get("cart", {}).get("items", [])),
                "shipping_method_code": (body.get("shipping_method") or {}).get("code"),
                "payment_method_code": (body.get("payment_method") or {}).get("code"),
                "attachments": [{"file_id": a.pk, "name": a.name} for a in order.orderattachment_set.all()],
            }
        )

    next_url = f"?page={page + 1}&page_size={page_size}" if offset + page_size < total else None
    prev_url = f"?page={page - 1}&page_size={page_size}" if page > 1 else None

    return results, total, next_url, prev_url
