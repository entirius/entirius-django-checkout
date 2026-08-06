# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""PayU notifications were accepted from anyone.

``payu_notify`` had no signature check, no IP allowlist and no channel key: a POST of
``{"order":{"orderId":"...","status":"COMPLETED"}}`` marked an order paid, and
``CANCELED`` cancelled someone else's order and released its stock reservation. The buyer
sees their own PayU ``orderId`` in the redirect to the gateway, so the id was not a secret.

Every other gateway in this module already verified — Paynow compares a Signature header,
Stripe uses construct_event, Przelewy24 calls back to verify the transaction. PayU was the
outlier.

Non-200 on failure is deliberate and load-bearing: PayU retries for 72h on any other code,
so a channel whose ``second_key`` is not configured yet gets a window to fix it rather than
silently losing payment confirmations.
"""

import hashlib
import json

import pytest
from django.test import RequestFactory

from django_checkout.domain.payment_provider.payu_signature import is_valid_notification, parse_signature_header
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider.payu import payu_notify

factory = RequestFactory()

SECOND_KEY = "synthetic-second-key"  # noqa: S105 — synthetic fixture
PAYU_ORDER_ID = "SYNTHETIC1234"


def _header(signature, algorithm="MD5"):
    return f"sender=checkout;signature={signature};algorithm={algorithm};content=DOCUMENT"


def _signed(body: bytes, second_key=SECOND_KEY, algorithm="MD5"):
    hasher = {"MD5": hashlib.md5, "SHA-1": hashlib.sha1, "SHA-256": hashlib.sha256}[algorithm]
    return _header(hasher(body + second_key.encode()).hexdigest(), algorithm)


def _body(status="COMPLETED", order_id=PAYU_ORDER_ID) -> bytes:
    return json.dumps({"order": {"orderId": order_id, "status": status}}).encode()


# --- Header parsing ---------------------------------------------------------------------


def test_parse_signature_header():
    parsed = parse_signature_header("sender=checkout;signature=abc123;algorithm=MD5;content=DOCUMENT")

    assert parsed == {"sender": "checkout", "signature": "abc123", "algorithm": "MD5", "content": "DOCUMENT"}


def test_parse_tolerates_spacing_and_case():
    parsed = parse_signature_header(" Sender=checkout ; SIGNATURE=abc ; algorithm=md5 ")

    assert parsed["signature"] == "abc"
    assert parsed["algorithm"] == "md5"


def test_parse_ignores_junk_segments():
    assert parse_signature_header("garbage;signature=abc") == {"signature": "abc"}


# --- Signature verification -------------------------------------------------------------


@pytest.mark.parametrize("algorithm", ["MD5", "SHA-1", "SHA-256"])
def test_accepts_every_documented_algorithm(algorithm):
    body = _body()

    assert is_valid_notification(body, _signed(body, algorithm=algorithm), SECOND_KEY) is True


def test_rejects_signature_from_a_different_key():
    body = _body()

    assert is_valid_notification(body, _signed(body, second_key="wrong-key"), SECOND_KEY) is False


def test_rejects_when_body_was_tampered_with():
    """The exploit: keep a captured signature, swap the status."""
    signature = _signed(_body(status="PENDING"))

    assert is_valid_notification(_body(status="COMPLETED"), signature, SECOND_KEY) is False


def test_signature_covers_the_exact_bytes_received():
    """Re-serializing the parsed JSON changes the bytes, so the hash must use raw bytes.

    The fixture is compact (no spaces after ``:`` / ``,``) because json.dumps' default
    separators are ``", "`` and ``": "``. Written out with those spaces already applied,
    the round-trip is byte-identical and this test proves nothing.
    """
    raw = b'{"order":{"status":"COMPLETED","orderId":"SYNTHETIC1234"}}'
    reserialized = json.dumps(json.loads(raw)).encode()
    assert raw != reserialized

    assert is_valid_notification(raw, _signed(raw), SECOND_KEY) is True
    assert is_valid_notification(reserialized, _signed(raw), SECOND_KEY) is False


@pytest.mark.parametrize(
    "header,second_key",
    [
        (None, SECOND_KEY),  # no header at all
        ("", SECOND_KEY),
        ("sender=checkout;algorithm=MD5", SECOND_KEY),  # no signature field
        ("sender=checkout;signature=abc", SECOND_KEY),  # no algorithm field
        ("sender=checkout;signature=abc;algorithm=ROT13", SECOND_KEY),  # unknown algorithm
        (_header("abc"), None),  # channel not configured
        (_header("abc"), ""),
    ],
)
def test_anything_missing_or_unknown_is_a_failure(header, second_key):
    """An unconfigured or unparseable notification must never read as "let it through"."""
    assert is_valid_notification(_body(), header, second_key) is False


# --- The endpoint -----------------------------------------------------------------------


@pytest.fixture
def payu_method(channel):
    return PaymentMethod.objects.create(
        channel=channel,
        code="payu",
        name_t9n={"en": "PayU"},
        additional_data={"pos_id": "1", "client_id": "1", "client_secret": "x", "second_key": SECOND_KEY},
    )


@pytest.fixture
def order(channel, customer):
    return Order.objects.create(
        channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={"total": "10.00"}
    )


@pytest.fixture
def payment_intent(order, payu_method):
    return PaymentIntent.objects.create(
        order=order,
        method=payu_method,
        code="payu",
        external_order_id=PAYU_ORDER_ID,
        payment_status=PaymentIntentStatus.NEW,
    )


def _notify(body: bytes, header=None):
    extra = {"HTTP_OPENPAYU_SIGNATURE": header} if header else {}
    request = factory.post("/notify/payu/", data=body, content_type="application/json", **extra)
    return payu_notify(request)


@pytest.mark.django_db
def test_forged_notification_cannot_mark_an_order_paid(payment_intent):
    """The vulnerability, end to end."""
    body = _body(status="COMPLETED")

    response = _notify(body)  # no signature at all

    assert response.status_code == 403
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    assert payment_intent.payment_status == PaymentIntentStatus.NEW
    assert payment_intent.order.order_status == OrderStatus.UNPAID


@pytest.mark.django_db
def test_forged_cancel_cannot_release_someone_elses_order(payment_intent):
    body = _body(status="CANCELED")

    response = _notify(body, _signed(body, second_key="attacker-key"))

    assert response.status_code == 403
    payment_intent.order.refresh_from_db()
    assert payment_intent.order.order_status == OrderStatus.UNPAID


@pytest.mark.django_db
def test_correctly_signed_notification_is_processed(payment_intent):
    """Counterpart to the rejection tests: a genuine notification must still get through."""
    body = _body(status="PENDING")
    assert payment_intent.payment_status == PaymentIntentStatus.NEW

    response = _notify(body, _signed(body))

    assert response.status_code == 200
    payment_intent.refresh_from_db()
    assert payment_intent.payment_status == PaymentIntentStatus.PENDING


@pytest.mark.django_db
def test_channel_without_second_key_rejects_instead_of_trusting(payment_intent, payu_method):
    """Missing config must fail closed — PayU will retry while it is fixed."""
    payu_method.additional_data = {"pos_id": "1", "client_id": "1", "client_secret": "x"}
    payu_method.save()
    body = _body()

    response = _notify(body, _signed(body))

    assert response.status_code == 403
    payment_intent.order.refresh_from_db()
    assert payment_intent.order.order_status == OrderStatus.UNPAID


@pytest.mark.django_db
def test_unknown_order_asks_payu_to_retry(channel):
    """A notification can outrun the commit that creates the PaymentIntent. Answering 200
    consumed the confirmation for good; PayU retries for 72h on anything else."""
    body = _body(order_id="NEVER-SEEN")

    response = _notify(body, _signed(body))

    assert response.status_code != 200


@pytest.mark.django_db
def test_malformed_body_is_not_retried(payment_intent):
    """Garbage is unprocessable, not transient — 200 stops PayU resending it for 72h."""
    response = _notify(b"not json at all", _signed(b"not json at all"))

    assert response.status_code == 200
