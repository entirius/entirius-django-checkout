# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v1 cart endpoints — payment-secret redaction and erasure.

``cart_body`` is the blob that becomes ``order_body``, and it holds the same credentials:
``card``, ``authorization_token``, ``continue_url``, ``pay_code``. Every v1 cart response
splats it wholesale (``CartResponse(**record.cart_body, ...)``), so redacting only the order
endpoints left the source wide open. Guest carts are bearer-UUID, so anyone who obtains a
``cart_id`` could read the gateway token.

The response assertions are structural rather than functional on purpose: ``CartResponse``
is a dataclass over the full ``CheckoutData`` field set, so building one in a test would
pin the DTO's entire shape and break on every unrelated field addition. What actually
regressed here is "a call site forgot to wrap the splat" — across seven of them, and the
next new endpoint makes eight. That is what the guard test checks.
"""

import re
from pathlib import Path

import pytest

import django_checkout.views.cart as cart_views
from django_checkout.models import Cart
from django_checkout.utils import REDACTED

CARD_TOKEN = "TOK_syntheticMultiUseToken"  # noqa: S105 — synthetic fixture
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiJ9.synthetic-authorization-token"  # noqa: S105 — synthetic fixture
CONTINUE_URL = "https://pay.example.com/continue/<order_id>?sig=synthetic-signature"
BLIK_CODE = "123456"
VOUCHER_CODE = "26WT****"

# `**<something>.cart_body` — the splat that ships the blob straight into a response.
# Matches a cart_body splat in either form:
#   **record.cart_body                          <- raw, leaks payment secrets
#   **redact_payment_secrets(record.cart_body)  <- wrapped, safe
# The optional call prefix matters: every real call site today is the wrapped form, so a
# pattern that only matched the raw form found nothing and made the guard below vacuous.
RAW_SPLAT = re.compile(r"\*\*\s*(?:\w+\s*\(\s*)?\w+\.cart_body\b")


def _secret_payment():
    return {
        "code": "card",
        "name": "Card",
        "card": CARD_TOKEN,
        "authorization_token": AUTH_TOKEN,
        "continue_url": CONTINUE_URL,
        "pay_code": BLIK_CODE,
    }


def _cart(channel, payment_method, **body):
    cart_body = {"currency_code": "EUR", "payment_method": payment_method}
    cart_body.update(body)
    return Cart.objects.create(channel=channel, cart_body=cart_body)


def test_no_cart_response_splats_the_body_unredacted():
    """Seven call sites today; the eighth must not be able to forget."""
    source = Path(cart_views.__file__).read_text()

    unwrapped = [
        line.strip() for line in source.splitlines() if RAW_SPLAT.search(line) and "redact_payment_secrets" not in line
    ]

    assert unwrapped == [], f"cart_body splatted into a response without redaction: {unwrapped}"


def test_every_response_splat_is_actually_wrapped():
    """Counterpart to the above — proves the guard is looking at real call sites."""
    source = Path(cart_views.__file__).read_text()

    assert len(RAW_SPLAT.findall(source)) >= 7


# --- Erasure (GDPR) --------------------------------------------------------------------


@pytest.mark.django_db
def test_cart_anonymize_scrubs_payment_secrets(channel):
    cart = _cart(channel, [_secret_payment()])

    assert cart.anonymize() is True

    cart.refresh_from_db()
    entry = cart.cart_body["payment_method"][0]
    assert entry["card"] == REDACTED
    assert entry["authorization_token"] == REDACTED
    assert entry["continue_url"] == REDACTED
    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_cart_anonymize_keeps_the_masked_voucher_code(channel):
    cart = _cart(channel, [{"code": "voucher", "pay_code": VOUCHER_CODE}])

    cart.anonymize()

    cart.refresh_from_db()
    assert cart.cart_body["payment_method"][0]["pay_code"] == VOUCHER_CODE


@pytest.mark.django_db
def test_cart_anonymize_still_scrubs_addresses(channel):
    """The pre-existing address scrub must survive alongside the new payment scrub."""
    cart = _cart(
        channel, [_secret_payment()], addresses={"billing_address": {"firstname": "Jan", "lastname": "Testowy"}}
    )

    cart.anonymize()

    cart.refresh_from_db()
    billing = cart.cart_body["addresses"]["billing_address"]
    assert billing["firstname"] == "xxx"
    assert billing["lastname"] == "xxxxxxx"


@pytest.mark.django_db
def test_cart_without_payment_method_is_left_alone(channel):
    """No payment entry and no address means nothing to save."""
    cart = _cart(channel, None)

    assert cart.anonymize() is False
