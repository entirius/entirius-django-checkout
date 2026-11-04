# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stripe checkout session events: which ones may change a payment, and how far.

An event is verified with the endpoint secret of the channel in the URL; without a non-empty
secret nothing is verified, so nothing passes.

Events are signed the way Stripe signs them (``t=...,v1=HMAC-SHA256``) and go through the real
``stripe.Webhook.construct_event``; nothing leaves the test.
"""

import hashlib
import hmac
import json
import time

import pytest
import stripe
from django.test import RequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider import stripe as stripe_views

factory = RequestFactory()

ENDPOINT_SECRET = "synthetic-endpoint-secret"
SESSION_ID = "SESSION-001"
COMPLETED, EXPIRED = "checkout.session.completed", "checkout.session.expired"


@pytest.fixture(autouse=True)
def no_global_api_key(monkeypatch):
    """``stripe.api_key`` is process-wide; keep one test's value from reaching another."""
    monkeypatch.setattr(stripe, "api_key", None)


def _stripe_method(channel, **additional_data):
    data = {"api_key": "synthetic-api-key", "endpoint_secret": ENDPOINT_SECRET, **additional_data}
    return PaymentMethod.objects.create(
        channel=channel, code="stripe", provider=PaymentProvider.STRIPE, name_t9n={"en": "Stripe"}, additional_data=data
    )


@pytest.fixture
def stripe_method(channel):
    return _stripe_method(channel)


def _signed_request(payload: str, secret: str = ENDPOINT_SECRET, header: str | None = None):
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    meta = {} if header == "" else {"HTTP_STRIPE_SIGNATURE": header or f"t={timestamp},v1={signature}"}
    return factory.post("/notify/stripe/", data=payload, content_type="application/json", **meta)


def _event(event_type: str, payment_status: str) -> str:
    session = {"id": SESSION_ID, "object": "checkout.session", "payment_status": payment_status}
    return json.dumps({"id": "EVT-001", "object": "event", "type": event_type, "data": {"object": session}})


def _notify(channel, event_type: str = COMPLETED, payment_status: str = "paid", **signing):
    return stripe_views.stripe_notify(
        _signed_request(_event(event_type, payment_status), **signing), channel_idx=channel.idx
    )


def _intent(channel, customer, method, payment_status=PaymentIntentStatus.PENDING, order_status=OrderStatus.UNPAID):
    order = Order.objects.create(channel=channel, customer=customer, order_status=order_status, order_body={})
    return PaymentIntent.objects.create(
        order=order, method=method, code=method.code, external_order_id=SESSION_ID, payment_status=payment_status
    )


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


UNTOUCHED = (PaymentIntentStatus.PENDING, OrderStatus.UNPAID)


# --- A verified event -----------------------------------------------------------------


@pytest.mark.django_db
def test_signed_event_completes_the_payment(channel, customer, stripe_method):
    payment_intent = _intent(channel, customer, stripe_method)

    response = _notify(channel)

    assert response.status_code == 200
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
def test_expired_session_cancels_the_payment_and_stores_the_session(channel, customer, stripe_method):
    payment_intent = _intent(channel, customer, stripe_method)

    response = _notify(channel, EXPIRED, "unpaid")

    assert response.status_code == 200
    assert _state(payment_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)
    assert payment_intent.provider_notify == {
        "id": SESSION_ID,
        "object": "checkout.session",
        "payment_status": "unpaid",
    }


# --- The endpoint secret -----------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("additional_data", [{"endpoint_secret": ""}, {"endpoint_secret": None}])
def test_method_without_an_endpoint_secret_rejects(channel, customer, additional_data):
    payment_intent = _intent(channel, customer, _stripe_method(channel, **additional_data))

    response = _notify(channel, secret="")

    assert response.status_code == 400
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_a_global_api_key_does_not_stand_in_for_the_secret(channel, customer, monkeypatch):
    """``stripe.api_key`` is set by whichever request ran before; it verifies nothing."""
    monkeypatch.setattr(stripe, "api_key", "synthetic-key-from-an-earlier-request")
    method = _stripe_method(channel)
    method.additional_data = {"api_key": "synthetic-api-key"}
    method.save()
    payment_intent = _intent(channel, customer, method)

    response = _notify(channel, secret="")

    assert response.status_code == 400
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_notification_leaves_the_global_api_key_alone(channel, customer, stripe_method):
    _intent(channel, customer, stripe_method)

    _notify(channel)

    assert stripe.api_key is None


@pytest.mark.django_db
@pytest.mark.parametrize("header", ["", "t=1,v1=0000"], ids=["missing", "wrong"])
def test_event_without_a_valid_signature_is_rejected(channel, customer, stripe_method, header):
    payment_intent = _intent(channel, customer, stripe_method)

    response = _notify(channel, header=header)

    assert response.status_code == 400
    assert _state(payment_intent) == UNTOUCHED
