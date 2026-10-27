# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid

from pydantic import ValidationError as PydanticValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response

from django_checkout.domain.dto.discount import DiscountsRejectedError
from django_checkout.enums import ItemStatus


def raise_pydantic_as_drf(exc: PydanticValidationError) -> None:
    """Convert Pydantic ValidationError to DRF ValidationError for v2 error handler."""
    errors = {}
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"]) if error["loc"] else "__all__"
        errors.setdefault(field, []).append(error["msg"])
    raise DRFValidationError(errors)


def pydantic_error_response(exc: PydanticValidationError) -> Response:
    """Convert Pydantic ValidationError to v2 error Response.

    Returns a DRF Response directly instead of raising — works with
    Volkanos custom exception handler which doesn't handle DRF ValidationError.
    """
    details = []
    for error in exc.errors():
        field = ".".join(str(loc) for loc in error["loc"]) if error["loc"] else None
        details.append(
            {
                "scope": "request",
                "field": field,
                "code": "VALIDATION_ERROR",
                "message": _sanitize_pydantic_message(error["msg"]),
                "meta": None,
            }
        )
    return Response(
        {
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed.",
            "debug_id": make_debug_id(),
            "details": details,
        },
        status=400,
    )


def _sanitize_pydantic_message(msg: str) -> str:
    """Strip internal type hints from Pydantic error messages to prevent schema leakage."""
    if "type=" in msg and "input_value=" in msg:
        # "Input should be a valid integer [type=int_type, input_value='abc', input_type=str]"
        return msg.split("[")[0].strip()
    return msg


def make_debug_id() -> str:
    return uuid.uuid4().hex[:8]


def map_to_v2_details(messages: list) -> list[dict]:
    """Convert v1 messages/ErrorInfo mix to v2 error details array.

    Domain logic (process_cart) returns a mixed list of:
    - str: plain text warnings
    - ErrorInfo: structured errors with code, affected_field, message

    This mapper normalizes them to v2 format.
    """
    details = []
    for msg in messages:
        if isinstance(msg, str):
            details.append(
                {
                    "scope": "cart",
                    "field": None,
                    "code": "GENERAL_WARNING",
                    "message": msg,
                    "meta": None,
                }
            )
        elif hasattr(msg, "code"):
            details.append(
                {
                    "scope": _infer_scope(getattr(msg, "code", ""), getattr(msg, "affected_field", "")),
                    "field": getattr(msg, "affected_field", None),
                    "code": _normalize_code(getattr(msg, "code", "UNKNOWN")),
                    "message": getattr(msg, "message", str(msg)),
                    "meta": _build_meta(msg),
                }
            )
            if isinstance(msg, DiscountsRejectedError):
                details.extend(map_discount_errors(msg))
    return details


def map_item_status_errors(items: list) -> list[dict]:
    """Convert item status field to v2 error details for non-valid items."""
    details = []
    for i, item in enumerate(items):
        if not hasattr(item, "status"):
            continue
        status = item.status
        if status == ItemStatus.VALID:
            continue
        code_map = {
            ItemStatus.OUT_OF_STOCK: "ITEM_OUT_OF_STOCK",
            ItemStatus.INVALID: "ITEM_NOT_SALEABLE",
            ItemStatus.CHANGED_PRICE: "ITEM_PRICE_CHANGED",
            ItemStatus.CHANGED_QTY: "ITEM_QTY_CHANGED",
            ItemStatus.CHANGED_PRICE_AND_QTY: "ITEM_PRICE_AND_QTY_CHANGED",
        }
        v2_code = code_map.get(status, "ITEM_INVALID")
        details.append(
            {
                "scope": "items",
                "field": f"items[{i}].quantity",
                "code": v2_code,
                "message": f"Item {item.sku}: {status.value}",
                "meta": {"sku": item.sku, "status": status.value},
            }
        )
    return details


def _infer_scope(code: str, affected_field: str) -> str:
    code_lower = code.lower()
    field_lower = (affected_field or "").lower()
    if "discount" in code_lower or "gratis" in code_lower or "coupon" in code_lower:
        return "discounts"
    if "item" in code_lower or "product" in code_lower or "stock" in code_lower:
        return "items"
    if "shipping" in code_lower or "delivery" in code_lower:
        return "shipping"
    if "payment" in code_lower:
        return "payment"
    if "address" in code_lower or "email" in field_lower or "country" in field_lower:
        return "addresses"
    return "cart"


def _normalize_code(code: str) -> str:
    return code.upper().replace(" ", "_").replace("-", "_")


REASON_TO_V2_CODE = {
    "code_not_found": "DISCOUNT_CODE_NOT_FOUND",
    "duplicate_rule": "DISCOUNT_DUPLICATE_RULE",
    "currency_not_supported": "DISCOUNT_CURRENCY_NOT_SUPPORTED",
    "blocked_by_price_rules": "DISCOUNT_BLOCKED_BY_PRICE_RULES",
    "expired": "DISCOUNT_EXPIRED",
    "not_yet_active": "DISCOUNT_NOT_YET_ACTIVE",
    "usage_limit_reached": "DISCOUNT_USAGE_LIMIT_REACHED",
    "per_user_limit_reached": "DISCOUNT_PER_USER_LIMIT_REACHED",
    "wrong_channel": "DISCOUNT_WRONG_CHANNEL",
    "min_order_not_met": "DISCOUNT_MIN_ORDER_NOT_MET",
    "login_required": "DISCOUNT_LOGIN_REQUIRED",
    "first_order_only": "DISCOUNT_FIRST_ORDER_ONLY",
    "customer_not_eligible": "DISCOUNT_CUSTOMER_NOT_ELIGIBLE",
    "no_eligible_products": "DISCOUNT_NO_ELIGIBLE_PRODUCTS",
    "blocked_by_exclusive_rule": "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE",
    "gratis_sku_not_allowed": "GRATIS_SKU_NOT_ALLOWED",
    "gratis_qty_exceeded": "GRATIS_QTY_EXCEEDED",
    "gratis_qty_invalid": "GRATIS_QTY_INVALID",
    "gratis_tier_not_reached": "GRATIS_TIER_NOT_REACHED",
    "gratis_required_skus_missing": "GRATIS_REQUIRED_SKUS_MISSING",
    "gratis_no_thresholds": "GRATIS_NO_THRESHOLDS_FOR_CURRENCY",
    "gratis_no_eligible_products": "GRATIS_NO_ELIGIBLE_PRODUCTS",
}

# Meta keys a client may see. The domain meta also carries rule internals (``blocking_priority``, ``reason``).
_PUBLIC_META_KEYS = frozenset({"code", "currency", "expired_at", "active_from", "min_order_amount"})


def map_discount_errors(error: DiscountsRejectedError) -> list[dict]:
    """One v2 detail per rejected code: the reason as a v2 code, the code's place in ``codes[]``, public meta.

    A rejection without a reason (the discount worker invalidated it) maps to ``DISCOUNT_CODE_INVALID``.
    The message stays generic; the frontend words the reason from ``code``.
    """
    visible_codes = {*error.sent_codes, *error.applied_codes}
    positions = _code_positions(error.sent_codes, [discount.code for discount in error.rejected])
    return [
        {
            "scope": "discounts",
            "field": f"codes[{position}].code" if position is not None else None,
            "code": REASON_TO_V2_CODE.get(discount.rejection_reason, "DISCOUNT_CODE_INVALID"),
            "message": "Discount code is invalid.",
            "meta": _public_discount_meta(discount, visible_codes),
        }
        for discount, position in zip(error.rejected, positions, strict=True)
    ]


def _code_positions(sent_codes: list[str], rejected_codes: list[str]) -> list[int | None]:
    """Index in ``sent_codes`` of each rejected code.

    Of a code sent twice the first copy is the one that applies, so the rejections take its last copies.
    """
    slots = {}
    for code in set(rejected_codes):
        sent_at = [i for i, sent in enumerate(sent_codes) if sent == code]
        slots[code] = iter(sent_at[-rejected_codes.count(code) :])
    return [next(slots[code], None) for code in rejected_codes]


def _public_discount_meta(discount, visible_codes: set[str]) -> dict:
    """``blocking_code`` only when the client already sees it: a code it sent or a discount applied in this cart."""
    meta = discount.rejection_meta or {}
    public = {"code": discount.code, **{key: value for key, value in meta.items() if key in _PUBLIC_META_KEYS}}
    if meta.get("blocking_code") in visible_codes:
        public["blocking_code"] = meta["blocking_code"]
    return public


def _build_meta(msg) -> dict | None:
    meta = {}
    for attr in ("sku", "available_quantity", "requested_quantity", "old_price", "new_price", "max_quantity"):
        val = getattr(msg, attr, None)
        if val is not None:
            meta[attr] = val
    return meta or None
