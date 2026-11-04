# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Authenticity check for PayPal webhook notifications.

PayPal signs every webhook it delivers; only a notification PayPal confirms may change a payment.

Verification is a postback: the ``PAYPAL-*`` transmission headers, the webhook id configured for
the payment method and the event go to PayPal's verify-webhook-signature API, which answers
``SUCCESS`` or ``FAILURE``. Chosen over checking the RSA signature locally, which needs the signing
certificate downloaded from the URL named in the headers, its chain validated against PayPal's CA,
and a new crypto dependency. The postback uses the credentials the payment method already holds for
creating and capturing PayPal orders.

The webhook id is per webhook (PayPal dashboard: app -> Webhooks -> Webhook ID). It lives in the
PayPal ``PaymentMethod.additional_data`` under ``webhook_id``, next to ``client_id`` and
``client_secret``. It is not a secret; without it nothing can be verified, so nothing passes.

https://developer.paypal.com/api/rest/webhooks/rest/#link-verifysignature
"""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

import requests
from paypal_sdk.services.request import get_api_url
from process_logger import ProcessLogger

from django_checkout.domain.payment_provider.paypal_payment_provider import get_use_sandbox_setting

if TYPE_CHECKING:
    from django_checkout.models import PaymentMethod

WEBHOOK_ID_FIELD = "webhook_id"
OAUTH_RESOURCE = "/v1/oauth2/token"
VERIFY_RESOURCE = "/v1/notifications/verify-webhook-signature"
TIMEOUT_SECONDS = 10
VERIFIED = "SUCCESS"

# verify-webhook-signature field -> header PayPal delivers it in.
TRANSMISSION_HEADERS = {
    "auth_algo": "PAYPAL-AUTH-ALGO",
    "cert_url": "PAYPAL-CERT-URL",
    "transmission_id": "PAYPAL-TRANSMISSION-ID",
    "transmission_sig": "PAYPAL-TRANSMISSION-SIG",
    "transmission_time": "PAYPAL-TRANSMISSION-TIME",
}

logger = ProcessLogger("PAYMENT_PAYPAL_WEBHOOK_VERIFICATION")


def get_webhook_id(payment_method: "PaymentMethod") -> str | None:
    return (payment_method.additional_data or {}).get(WEBHOOK_ID_FIELD) or None


def transmission_fields(headers: Mapping[str, str]) -> dict[str, str] | None:
    """The signature headers as verify-webhook-signature fields, or None when any is missing."""
    fields = {field: headers.get(header) for field, header in TRANSMISSION_HEADERS.items()}
    return fields if all(fields.values()) else None


def build_verification_payload(raw_body: bytes, transmission: dict[str, str], webhook_id: str) -> str:
    """Request body for verify-webhook-signature, with the event embedded as the bytes received.

    The signature covers exactly what PayPal sent. A ``json.loads`` / ``json.dumps`` round trip
    can change those bytes (unicode escapes, whitespace), and PayPal then fails a genuine event.

    Splicing is safe only for a single JSON value: the caller has already parsed ``raw_body``, so
    nothing can follow it and close the envelope early. Header values go through ``json.dumps``.
    """
    envelope = json.dumps({**transmission, "webhook_id": webhook_id})
    return envelope[:-1] + ', "webhook_event": ' + raw_body.decode() + "}"


def _access_token(api_url: str, payment_method: "PaymentMethod") -> str:
    """OAuth token of the method's PayPal app.

    Not ``paypal_sdk.PayPal()``: its token request has no timeout, and this runs for every
    notification that reaches verification, so a slow PayPal would pin the worker.
    """
    response = requests.post(
        api_url + OAUTH_RESOURCE,
        data={"grant_type": "client_credentials"},
        auth=(payment_method.additional_data["client_id"], payment_method.additional_data["client_secret"]),
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def request_verification(payment_method: "PaymentMethod", payload: str) -> str | None:
    """PayPal's verdict on ``payload`` (``SUCCESS`` / ``FAILURE``), or None when PayPal could not be asked."""
    try:
        api_url = get_api_url(get_use_sandbox_setting(payment_method))
        token = _access_token(api_url, payment_method)
        response = requests.post(
            api_url + VERIFY_RESOURCE,
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json().get("verification_status")
    except Exception as e:
        logger.exception(e)
        return None


def is_valid_webhook(raw_body: bytes, headers: Mapping[str, str], payment_method: "PaymentMethod") -> bool:
    """True only when PayPal confirms it sent ``raw_body`` to ``payment_method``'s webhook.

    ``raw_body`` MUST be the bytes as received. Fails closed: no webhook id, a missing
    signature header or an unreachable PayPal API all read as "not from PayPal".
    """
    webhook_id = get_webhook_id(payment_method)
    transmission = transmission_fields(headers)
    if not webhook_id or transmission is None:
        return False
    try:
        payload = build_verification_payload(raw_body, transmission, webhook_id)
    except UnicodeDecodeError:
        return False
    return request_verification(payment_method, payload) == VERIFIED
