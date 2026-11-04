# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Authenticity check for Przelewy24 transaction notifications.

Przelewy24 signs every notification. ``sign`` is SHA-384 of the JSON object below, written without
whitespace between tokens and with non-ASCII characters as they are, where ``crc`` is the shop's
CRC key:

    {"merchantId":..,"posId":..,"sessionId":..,"amount":..,"originAmount":..,"currency":..,
     "orderId":..,"methodId":..,"statement":..,"crc":..}

Only a notification with a matching sign makes the notify endpoint ask Przelewy24's verify API.

https://developers.przelewy24.pl/ (REST API, transaction notification)
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING

from przelewy24_sdk.settings import CRC_KEY

if TYPE_CHECKING:
    from django_checkout.models import PaymentMethod

SIGNED_FIELDS = (
    "merchantId",
    "posId",
    "sessionId",
    "amount",
    "originAmount",
    "currency",
    "orderId",
    "methodId",
    "statement",
)


def crc_key_for(payment_method: "PaymentMethod") -> str | None:
    """The method's CRC key, falling back to the ``PRZELEWY24_CRC_KEY`` setting."""
    return (payment_method.additional_data or {}).get("crc_key", CRC_KEY)


def notification_sign(body: dict, crc_key: str) -> str:
    text = json.dumps(
        {**{field: body[field] for field in SIGNED_FIELDS}, "crc": crc_key}, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha384(text.encode()).hexdigest()


def is_valid_notification(body: dict, crc_key: str | None) -> bool:
    """True when ``body`` carries a ``sign`` made with ``crc_key``; anything missing is a failure."""
    sign = body.get("sign")
    if not crc_key or not isinstance(sign, str) or any(field not in body for field in SIGNED_FIELDS):
        return False
    # compare_digest, not ==: a plain comparison leaks the expected value through timing.
    return hmac.compare_digest(notification_sign(body, crc_key), sign)
