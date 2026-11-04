# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""PayPal endpoints: the buyer's return, the cancel return and the webhook.

A webhook changes a payment only when it is for a PayPal payment, the payment method carries the
``webhook_id`` PayPal shows for the webhook, and PayPal confirms the signature. Anything else is a
403 with nothing written. Non-200 on purpose: PayPal redelivers until it gets a 2xx, so a genuine
webhook rejected while a method is missing its webhook id is not lost.

PayPal is never called. ``FakePayPal`` answers the REST calls these endpoints make (OAuth token,
verify-webhook-signature, get order, capture); any other outbound HTTP fails the test.
"""

import json
import logging

import pytest
import requests
from django.test import RequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider import paypal as paypal_views
from django_checkout.views.payment_provider.paypal import paypal_notify, paypal_return

factory = RequestFactory()

WEBHOOK_ID = "SYNTHETIC-WEBHOOK-ID"
PAYPAL_ORDER_ID = "PAYPAL-ORDER-001"
OTHER_ORDER_ID = "OTHER-GATEWAY-ORDER-001"
VERIFY_PATH = "/v1/notifications/verify-webhook-signature"

TRANSMISSION = {
    "PAYPAL-AUTH-ALGO": "SHA256withRSA",
    "PAYPAL-CERT-URL": "https://api-m.paypal.com/v1/notifications/certs/CERT-SYNTHETIC",
    "PAYPAL-TRANSMISSION-ID": "TRANSMISSION-001",
    "PAYPAL-TRANSMISSION-SIG": "c3ludGhldGljLXNpZ25hdHVyZQ==",
    "PAYPAL-TRANSMISSION-TIME": "2026-01-01T12:00:00Z",
}


def _response(status_code: int, payload: dict | bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.encoding = "utf-8"
    response._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return response


class FakePayPal:
    """PayPal's REST API, as far as these endpoints touch it.

    ``token_answer`` and ``verify_answer`` are the (status, body) PayPal replies with; ``reachable = False``
    makes verify-webhook-signature time out; ``order_status`` is what get order reports. ``token_requests``
    and ``verifications`` record those requests (url, payload, headers, auth, timeout), ``captures`` the
    captured order urls, ``unexpected`` any other call.
    """

    TOKEN = {
        "scope": "https://uri.paypal.com/services/payments",
        "access_token": "synthetic-access-token",
        "token_type": "Bearer",
        "app_id": "APP-SYNTHETIC",
        "expires_in": 3600,
        "nonce": "synthetic-nonce",
    }

    def __init__(self):
        self.token_answer = (200, self.TOKEN)
        self.verify_answer = (200, {"verification_status": "SUCCESS"})
        self.reachable = True
        self.order_status = "CREATED"
        self.token_requests = []
        self.verifications = []
        self.captures = []
        self.unexpected = []

    def say(self, verdict: str) -> None:
        self.verify_answer = (200, {"verification_status": verdict})

    def __call__(self, method, url, **kwargs):
        if url.endswith("/v1/oauth2/token"):
            self.token_requests.append({"url": url, **kwargs})
            return _response(*self.token_answer)
        if url.endswith(VERIFY_PATH):
            self.verifications.append({"url": url, **kwargs})
            if not self.reachable:
                raise requests.Timeout("PayPal unreachable")
            return _response(*self.verify_answer)
        if method == "POST" and url.endswith(f"/v2/checkout/orders/{PAYPAL_ORDER_ID}/capture"):
            self.captures.append(url)
            return _response(201, {"id": PAYPAL_ORDER_ID, "status": "COMPLETED"})
        if method == "GET" and url.endswith(f"/v2/checkout/orders/{PAYPAL_ORDER_ID}"):
            return _response(200, {"id": PAYPAL_ORDER_ID, "status": self.order_status})
        self.unexpected.append(f"{method} {url}")
        raise AssertionError(f"Unexpected outbound call in a test: {method} {url}")


@pytest.fixture(autouse=True)
def paypal(monkeypatch):
    """Every HTTP call made through ``requests`` lands here, never on the network.

    The code under test may swallow the AssertionError of an unexpected call (broad ``except``
    blocks), so the record is checked again once the test is done.
    """
    fake = FakePayPal()
    monkeypatch.setattr(
        requests.sessions.Session, "request", lambda session, method, url, **kwargs: fake(method, url, **kwargs)
    )
    yield fake
    assert fake.unexpected == []


def _paypal_additional_data(**overrides):
    data = {
        "client_id": "synthetic-client-id",
        "client_secret": "synthetic-client-secret",
        "use_sandbox": True,
        "webhook_id": WEBHOOK_ID,
    }
    data.update(overrides)
    return data


@pytest.fixture
def paypal_method(channel):
    return PaymentMethod.objects.create(
        channel=channel,
        code="paypal",
        provider=PaymentProvider.PAYPAL,
        name_t9n={"en": "PayPal"},
        additional_data=_paypal_additional_data(),
    )


def _unpaid_order(channel, customer):
    return Order.objects.create(
        channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={"total": "10.00"}
    )


def _pending_intent(order, method, external_order_id):
    return PaymentIntent.objects.create(
        order=order,
        method=method,
        code=method.code,
        external_order_id=external_order_id,
        payment_status=PaymentIntentStatus.PENDING,
    )


@pytest.fixture
def paypal_intent(channel, customer, paypal_method):
    return _pending_intent(_unpaid_order(channel, customer), paypal_method, PAYPAL_ORDER_ID)


@pytest.fixture
def other_gateway_intent(request, channel, customer):
    """A PaymentIntent of a non-PayPal gateway; the provider comes from ``request.param``."""
    provider = getattr(request, "param", PaymentProvider.PAYU)
    method = PaymentMethod.objects.create(
        channel=channel,
        code=str(provider),
        provider=provider,
        name_t9n={"en": str(provider)},
        additional_data={"client_id": "1", "client_secret": "x", "webhook_id": WEBHOOK_ID},
    )
    return _pending_intent(_unpaid_order(channel, customer), method, OTHER_ORDER_ID)


def _capture_completed(order_id=PAYPAL_ORDER_ID) -> bytes:
    """A PAYMENT.CAPTURE.COMPLETED event as PayPal sends it: compact JSON, non-ASCII left as-is."""
    event = {
        "id": "WH-SYNTHETIC-001",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "CAPTURE-001",
            "status": "COMPLETED",
            "amount": {"currency_code": "EUR", "value": "10.00"},
            "supplementary_data": {"related_ids": {"order_id": order_id}},
            "note_to_payer": "Zoë",
        },
    }
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()


def _order_event(status: str) -> bytes:
    """A CHECKOUT.ORDER.* event: the PayPal order id sits in ``resource.id``."""
    event = {
        "id": "WH-SYNTHETIC-002",
        "event_type": f"CHECKOUT.ORDER.{status}",
        "resource": {"id": PAYPAL_ORDER_ID, "status": status},
    }
    return json.dumps(event, separators=(",", ":")).encode()


def _notify(body: bytes, headers: dict | None = None):
    meta = {f"HTTP_{name.replace('-', '_')}": value for name, value in (headers or {}).items()}
    request = factory.post("/notify/paypal/", data=body, content_type="application/json", **meta)
    return paypal_notify(request)


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


UNTOUCHED = (PaymentIntentStatus.PENDING, OrderStatus.UNPAID)


# --- Only PayPal changes a payment through the webhook -----------------------------------


@pytest.mark.django_db
def test_unsigned_notification_changes_nothing(paypal_intent, paypal):
    response = _notify(json.dumps({"resource": {"id": PAYPAL_ORDER_ID, "status": "COMPLETED"}}).encode())

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert paypal.verifications == []


@pytest.mark.django_db
@pytest.mark.parametrize("other_gateway_intent", [PaymentProvider.PAYU, PaymentProvider.STRIPE], indirect=True)
def test_notification_for_another_gateways_payment_changes_nothing(other_gateway_intent, paypal):
    """A signature PayPal would accept does not make a PayU or Stripe payment PayPal's."""
    response = _notify(_capture_completed(order_id=OTHER_ORDER_ID), TRANSMISSION)

    assert response.status_code == 403
    assert _state(other_gateway_intent) == UNTOUCHED
    assert paypal.verifications == []


@pytest.mark.django_db
def test_signature_paypal_rejects_changes_nothing(paypal_intent, paypal):
    paypal.say("FAILURE")

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert len(paypal.verifications) == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "answer",
    [
        (500, {"name": "INTERNAL_SERVER_ERROR"}),
        (401, {"error": "invalid_token"}),
        (200, {}),
        (200, {"verification_status": "success"}),
        (200, b"<html>not json</html>"),
    ],
    ids=["http-500", "http-401", "no-verdict", "lowercase-success", "not-json"],
)
def test_anything_but_an_exact_success_fails_closed(paypal_intent, paypal, answer):
    paypal.verify_answer = answer

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert len(paypal.verifications) == 1


@pytest.mark.django_db
def test_refused_oauth_token_fails_closed(paypal_intent, paypal):
    """Wrong client credentials: PayPal refuses the token, so there is nothing to verify with."""
    paypal.token_answer = (401, {"error": "invalid_client", "error_description": "Client Authentication failed"})

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert paypal.verifications == []


@pytest.mark.django_db
@pytest.mark.parametrize("missing", sorted(TRANSMISSION))
def test_missing_signature_header_is_rejected_without_asking_paypal(paypal_intent, paypal, missing):
    headers = {name: value for name, value in TRANSMISSION.items() if name != missing}

    response = _notify(_capture_completed(), headers)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert paypal.verifications == []


@pytest.mark.django_db
@pytest.mark.parametrize("webhook_id", [None, ""])
def test_method_without_webhook_id_rejects_instead_of_trusting(paypal_intent, paypal_method, paypal, webhook_id):
    """Missing config fails closed, it does not skip verification. PayPal redelivers while it is fixed."""
    paypal_method.additional_data = _paypal_additional_data(webhook_id=webhook_id)
    paypal_method.save()

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert paypal.verifications == []


@pytest.mark.django_db
def test_unreachable_paypal_fails_closed(paypal_intent, paypal):
    paypal.reachable = False

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED
    assert len(paypal.verifications) == 1


@pytest.mark.django_db
def test_intent_whose_payment_method_was_deleted_is_rejected(paypal_intent, paypal):
    """``PaymentIntent.method`` is SET_NULL: without it there is no webhook id to verify against."""
    PaymentIntent.objects.filter(pk=paypal_intent.pk).update(method=None)

    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 403
    assert _state(paypal_intent) == UNTOUCHED


class _LogSnapshot(logging.Handler):
    """Copies each record's ``details`` when it is emitted: ProcessLogger clears them right after."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.lines = []

    def emit(self, record):
        self.lines.append((record.getMessage(), dict(getattr(record, "details", {}))))


@pytest.mark.django_db
def test_rejection_log_line_carries_only_its_own_params(paypal_intent):
    """The logger is shared by the worker's requests; a body logged by an earlier one must not ride along."""
    paypal_views.logger_process.add_log_param("paypal_response", {"payer": "left over from an earlier request"})
    snapshot = _LogSnapshot()
    process_log = logging.getLogger("process")
    process_log.addHandler(snapshot)
    try:
        _notify(json.dumps({"resource": {"id": PAYPAL_ORDER_ID, "status": "COMPLETED"}}).encode())
    finally:
        process_log.removeHandler(snapshot)

    [(_, details)] = [line for line in snapshot.lines if "payment_intent_pk" in line[1]]
    assert set(details) == {"paypal_order_id", "payment_intent_pk", "is_status_changed"}


# --- A genuine webhook still gets through ------------------------------------------------


@pytest.mark.django_db
def test_correctly_signed_notification_completes_payment_and_confirms_order(paypal_intent):
    response = _notify(_capture_completed(), TRANSMISSION)

    assert response.status_code == 200
    assert _state(paypal_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "use_sandbox,api_url", [(True, "https://api-m.sandbox.paypal.com"), (False, "https://api-m.paypal.com")]
)
def test_verification_asks_paypal_about_this_exact_event(paypal_intent, paypal_method, paypal, use_sandbox, api_url):
    """PayPal checks the signature over the bytes it sent: the event must reach it verbatim,
    with this method's webhook id and the transmission headers, on the right environment."""
    paypal_method.additional_data = _paypal_additional_data(use_sandbox=use_sandbox)
    paypal_method.save()
    body = _capture_completed()

    _notify(body, TRANSMISSION)

    [verification] = paypal.verifications
    assert verification["url"] == api_url + VERIFY_PATH
    assert body.decode() in verification["data"]
    assert json.loads(verification["data"]) == {
        "auth_algo": TRANSMISSION["PAYPAL-AUTH-ALGO"],
        "cert_url": TRANSMISSION["PAYPAL-CERT-URL"],
        "transmission_id": TRANSMISSION["PAYPAL-TRANSMISSION-ID"],
        "transmission_sig": TRANSMISSION["PAYPAL-TRANSMISSION-SIG"],
        "transmission_time": TRANSMISSION["PAYPAL-TRANSMISSION-TIME"],
        "webhook_id": WEBHOOK_ID,
        "webhook_event": json.loads(body),
    }
    assert verification["headers"]["Authorization"] == "Bearer synthetic-access-token"
    assert verification["timeout"]
    [token_request] = paypal.token_requests
    assert token_request["url"] == api_url + "/v1/oauth2/token"
    assert token_request["auth"] == ("synthetic-client-id", "synthetic-client-secret")
    assert token_request["timeout"]


# --- The buyer's return from PayPal ------------------------------------------------------


def _buyer_return():
    return paypal_return(factory.get("/return/paypal/", {"token": PAYPAL_ORDER_ID, "PayerID": "PAYER-001"}))


@pytest.mark.django_db
def test_return_captures_and_confirms_the_order(paypal_intent, paypal):
    """The status comes from PayPal's capture API, not from the request."""
    response = _buyer_return()

    assert response.status_code == 200
    assert len(paypal.captures) == 1
    assert _state(paypal_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)
