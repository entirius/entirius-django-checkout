# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Paynow notifications change only a Paynow payment, and only with a matching signature.

The signature is compared in constant time; a notification naming another gateway's payment is
refused before its method's keys are read.
"""

import base64
import hashlib
import hmac
import json

import pytest
from django.test import RequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider.paynow import paynow_notify

factory = RequestFactory()

SIGNATURE_KEY = "synthetic-signature-key"
PAYNOW_PAYMENT_ID = "NOW-SYNTHETIC-001"
UNTOUCHED = (PaymentIntentStatus.PENDING, OrderStatus.UNPAID)


def _intent(channel, customer, provider=PaymentProvider.PAYNOW):
    method = PaymentMethod.objects.create(
        channel=channel,
        code=str(provider),
        provider=provider,
        name_t9n={"en": str(provider)},
        additional_data={"api_key": "synthetic-api-key", "signature_key": SIGNATURE_KEY},
    )
    order = Order.objects.create(channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={})
    return PaymentIntent.objects.create(
        order=order,
        method=method,
        code=method.code,
        external_order_id=PAYNOW_PAYMENT_ID,
        payment_status=PaymentIntentStatus.PENDING,
    )


def _signature(payload: str, key: str = SIGNATURE_KEY) -> str:
    return base64.b64encode(hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()).decode()


def _notify(signature: str | None = None, status: str = "CONFIRMED"):
    body = {"paymentId": PAYNOW_PAYMENT_ID, "externalId": "SYNTHETIC-EXTERNAL", "status": status}
    payload = json.dumps(body, separators=(",", ":"))
    signature = _signature(payload) if signature is None else signature
    request = factory.post("/notify/paynow/", data=payload, content_type="application/json", HTTP_SIGNATURE=signature)
    return paynow_notify(request)


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


@pytest.mark.django_db
def test_signed_notification_completes_the_payment(channel, customer):
    payment_intent = _intent(channel, customer)

    response = _notify()

    assert response.status_code == 200
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "signature",
    ["", _signature("{}"), _signature("{}", key="synthetic-other-key"), "zażółć"],
    ids=["empty", "other-body", "other-key", "non-ascii"],
)
def test_notification_without_a_matching_signature_changes_nothing(channel, customer, signature):
    payment_intent = _intent(channel, customer)

    response = _notify(signature=signature)

    assert response.status_code == 404
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_notification_cannot_touch_another_gateways_payment(channel, customer):
    """Even when that payment's method happens to carry keys under the same names."""
    payment_intent = _intent(channel, customer, provider=PaymentProvider.STRIPE)

    response = _notify()

    assert response.status_code == 404
    assert _state(payment_intent) == UNTOUCHED


# --- What is stored ---------------------------------------------------------------------


@pytest.mark.django_db
def test_stored_notification_keeps_ids_and_status_only(channel, customer):
    """Paynow sends no buyer data in a notification; what it sends is kept, and verified on the raw body first."""
    payment_intent = _intent(channel, customer)

    response = _notify()

    assert response.status_code == 200
    payment_intent.refresh_from_db()
    assert payment_intent.provider_notify == {
        "paymentId": PAYNOW_PAYMENT_ID,
        "externalId": "SYNTHETIC-EXTERNAL",
        "status": "CONFIRMED",
    }
