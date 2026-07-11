# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build v2 cart response from Cart record.

Loads CheckoutData from cart_body, maps through Pydantic CartV2Response,
adds computed properties (free shipping, split, tax rates) from Cart model.
"""

import logging
from dataclasses import asdict
from decimal import Decimal

from django_checkout.api.v2.exceptions import map_item_status_errors, map_to_v2_details
from django_checkout.models import Cart
from django_checkout.schemas.responses.cart import CartV2Response
from django_checkout.utils import sanitize

logger = logging.getLogger("checkout.v2.response_builder")


def _safe(fn, default, label: str = ""):
    """Call fn(), return default on failure. Logs the exception for observability."""
    try:
        return fn()
    except Exception:
        if label:
            logger.debug("Cart property %s failed (marshmallow Union issue), using default", label)
        return default


def build_v2_cart_response(record: Cart, messages: list | None = None) -> dict:
    """Build complete v2 cart response dict from a Cart record."""
    from django_checkout.services.cart_service import _load_checkout_data

    checkout_data = _load_checkout_data(record)

    # Cart model properties call as_data internally which can fail with marshmallow
    # Union deserialization on addresses. _safe() logs and returns default.
    response = CartV2Response.from_domain(
        checkout_data,
        cart_id=str(record.cart_id),
        cart_status=record.cart_status,
        free_shipping=_safe(lambda: record.is_eglible_for_free_shipping or False, False, "free_shipping"),
        can_be_split=_safe(lambda: record.is_egible_for_split or False, False, "can_be_split"),
        amount_required_for_free_shipping=_safe(lambda: record.amount_required_for_free_shipping, None),
        amount_missing_for_free_shipping=_safe(lambda: record.amount_missing_for_free_shipping, None),
        tax_rates=_safe(lambda: record.tax_rates, {}, "tax_rates"),
        min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")) if record.channel else None,
    )

    result = response.model_dump(exclude_none=False)

    if messages:
        details = map_to_v2_details(messages)
        if checkout_data.cart and checkout_data.cart.items:
            details.extend(map_item_status_errors(checkout_data.cart.items))
        if details:
            result["_errors"] = details

    return result


def build_v2_cart_response_fresh(record: Cart) -> dict:
    """Build a v2 cart response with automatic discounts re-discovered.

    GET endpoints serialize the stored ``cart_body`` as-is, but automatic
    discount rules (``automatic_applications=True``) are only applied in the
    write path (``process_cart``). This variant recomputes in-memory — no DB
    write — so a plain read reflects the same discounts/totals a write would,
    keeping cart-returning endpoints consistent.

    The recompute result is assigned to ``record.cart_body`` in memory only
    (never saved) so the Cart's computed properties (free shipping, tax rates)
    read the fresh data. On any recompute failure it falls back to the stored
    snapshot — a read must not 500 because of discount processing.
    """
    from django_checkout.services import cart_service

    try:
        processed = cart_service.recompute_checkout_data(record)
        record.cart_body = sanitize(asdict(processed))
    except Exception:
        logger.exception("Fresh recompute failed for cart %s, serving stored snapshot", record.cart_id)
    return build_v2_cart_response(record)
