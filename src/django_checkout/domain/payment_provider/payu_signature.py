# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Authenticity check for PayU order notifications.

PayU signs every notification with an ``OpenPayu-Signature`` header. Without checking it
the notify endpoint accepts a status change from anyone who knows the PayU ``orderId`` —
which the buyer sees in their own redirect to the gateway, so they can confirm their own
payment or cancel someone else's order and release its stock reservation.

Header shape, per PayU:

    OpenPayu-Signature: sender=checkout;signature=<hex>;algorithm=MD5;content=DOCUMENT

The signature is ``hash(raw_notification_body + second_key)``. The second key comes from
the merchant panel and is NOT ``client_secret`` (that one is for OAuth when creating a
payment).

https://developers.payu.com/europe/pl/docs/payment-flows/lifecycle/#notification-examples
"""

import hashlib
import hmac

SIGNATURE_HEADER = "OpenPayu-Signature"
SECOND_KEY_FIELD = "second_key"

# PayU sends MD5 today, but the header names the algorithm, so read it instead of
# assuming — a switch on their side would otherwise reject every notification. MD5 is
# their protocol's choice, not ours.
_ALGORITHMS = {
    "MD5": hashlib.md5,
    "SHA-1": hashlib.sha1,
    "SHA1": hashlib.sha1,
    "SHA-256": hashlib.sha256,
    "SHA256": hashlib.sha256,
}


def parse_signature_header(header: str) -> dict[str, str]:
    """``sender=checkout;signature=abc;algorithm=MD5`` -> ``{"sender": "checkout", ...}``.

    Keys are lower-cased; values are not, since the signature is hex and the algorithm is
    matched case-insensitively at the call site.
    """
    fields = {}
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator:
            fields[key.strip().lower()] = value.strip()
    return fields


def is_valid_notification(raw_body: bytes, header: str | None, second_key: str | None) -> bool:
    """True when ``raw_body`` really was signed by PayU with ``second_key``.

    ``raw_body`` MUST be the bytes as received. The hash covers exactly what PayU sent, so
    hashing a re-serialized parse of the JSON would never match — key order and whitespace
    would differ.

    Anything missing or unrecognised is a failure: an unconfigured second key must not read
    as "let it through".
    """
    if not header or not second_key:
        return False

    fields = parse_signature_header(header)
    algorithm = _ALGORITHMS.get(fields.get("algorithm", "").upper())
    signature = fields.get("signature")
    if algorithm is None or not signature:
        return False

    expected = algorithm(raw_body + second_key.encode()).hexdigest()
    # compare_digest, not ==: a plain comparison leaks the expected value through timing.
    return hmac.compare_digest(expected, signature)
