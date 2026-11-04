# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""PayPal endpoints: the buyer's return, the cancel return and the webhook.

PayPal is never called. ``FakePayPal`` answers the REST calls these endpoints make (OAuth token,
get order, capture); any other outbound HTTP fails the test.
"""

import json

import pytest
import requests
from django.test import RequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider.paypal import paypal_return

factory = RequestFactory()

PAYPAL_ORDER_ID = "PAYPAL-ORDER-001"


def _response(status_code: int, payload: dict | bytes) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.encoding = "utf-8"
    response._content = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return response


class FakePayPal:
    """PayPal's REST API, as far as these endpoints touch it.

    ``order_status`` is what get order reports. ``token_requests`` records the OAuth requests (url, payload,
    auth, timeout), ``captures`` the captured order urls, ``unexpected`` any other call.
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
        self.order_status = "CREATED"
        self.token_requests = []
        self.captures = []
        self.unexpected = []

    def __call__(self, method, url, **kwargs):
        if url.endswith("/v1/oauth2/token"):
            self.token_requests.append({"url": url, **kwargs})
            return _response(*self.token_answer)
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
    data = {"client_id": "synthetic-client-id", "client_secret": "synthetic-client-secret", "use_sandbox": True}
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


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


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
