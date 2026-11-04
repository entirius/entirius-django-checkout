# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Paynow notifications only move a payment forward.

Paynow redelivers notifications and does not keep their order. A signed status is applied through
``domain/payment_provider/notification_state.py``: a COMPLETE payment is final, a CANCELLED one
only gives way to a confirmed payment, and an order is confirmed or cancelled only while it waits
for payment.
"""

import base64
import hashlib
import hmac
import json

import pytest
from django.test import RequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.signals import order_confirmed_signal
from django_checkout.views.payment_provider import paynow as paynow_views
from django_checkout.views.payment_provider.paynow import paynow_notify

factory = RequestFactory()

SIGNATURE_KEY = "synthetic-signature-key"
PAYNOW_PAYMENT_ID = "NOW-SYNTHETIC-001"


@pytest.fixture
def paynow_intent(channel, customer):
    method = PaymentMethod.objects.create(
        channel=channel,
        code="paynow",
        provider=PaymentProvider.PAYNOW,
        name_t9n={"en": "Paynow"},
        additional_data={"api_key": "synthetic-api-key", "signature_key": SIGNATURE_KEY},
    )
    order = Order.objects.create(
        channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={"total": "10.00"}
    )
    return PaymentIntent.objects.create(
        order=order,
        method=method,
        code=method.code,
        external_order_id=PAYNOW_PAYMENT_ID,
        payment_status=PaymentIntentStatus.PENDING,
    )


def _notify(status: str):
    body = {"paymentId": PAYNOW_PAYMENT_ID, "externalId": "SYNTHETIC-EXTERNAL", "status": status}
    payload = json.dumps(body, separators=(",", ":"))
    digest = hmac.new(SIGNATURE_KEY.encode(), payload.encode(), hashlib.sha256).digest()
    request = factory.post(
        "/notify/paynow/",
        data=payload,
        content_type="application/json",
        HTTP_SIGNATURE=base64.b64encode(digest).decode(),
    )
    return paynow_notify(request)


def _set_state(payment_intent, payment_status, order_status):
    PaymentIntent.objects.filter(pk=payment_intent.pk).update(payment_status=payment_status)
    Order.objects.filter(pk=payment_intent.order_id).update(order_status=order_status)


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


@pytest.mark.django_db
def test_confirmed_payment_confirms_the_order(paynow_intent):
    response = _notify("CONFIRMED")

    assert response.status_code == 200
    assert _state(paynow_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
def test_rejected_payment_cancels_an_order_still_waiting_for_payment(paynow_intent):
    _notify("REJECTED")

    assert _state(paynow_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)


@pytest.mark.django_db
@pytest.mark.parametrize("order_status", [OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED, OrderStatus.RETURNED])
def test_redelivered_confirmation_keeps_an_order_that_moved_on(paynow_intent, order_status):
    _set_state(paynow_intent, PaymentIntentStatus.COMPLETE, order_status)
    sent = []
    order_confirmed_signal.connect(lambda **kwargs: sent.append(kwargs), weak=False, dispatch_uid="paynow-test")
    try:
        _notify("CONFIRMED")
    finally:
        order_confirmed_signal.disconnect(dispatch_uid="paynow-test")

    assert _state(paynow_intent) == (PaymentIntentStatus.COMPLETE, order_status)
    assert sent == []


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["PENDING", "NEW", "REJECTED", "ERROR", "EXPIRED"])
def test_late_status_leaves_a_completed_payment_alone(paynow_intent, status):
    _set_state(paynow_intent, PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)

    response = _notify(status)

    assert response.status_code == 200
    assert _state(paynow_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
def test_rejection_leaves_an_order_confirmed_by_hand(paynow_intent):
    _set_state(paynow_intent, PaymentIntentStatus.PENDING, OrderStatus.CONFIRMED)

    _notify("REJECTED")

    assert _state(paynow_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CONFIRMED)


@pytest.mark.django_db
def test_confirmation_after_cancellation_confirms_the_order(paynow_intent):
    """Paid wins over an earlier cancellation (logged for review: the stock reservation is gone)."""
    _set_state(paynow_intent, PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)

    _notify("CONFIRMED")

    assert _state(paynow_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["PENDING", "ERROR"])
def test_cancelled_payment_ignores_anything_but_confirmation(paynow_intent, status):
    _set_state(paynow_intent, PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)

    _notify(status)

    assert _state(paynow_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,finish_tag",
    [
        ("CONFIRMED", "PaymentIntent statuet to COMPLETE, Order status is set to CONFIRMED"),
        ("REJECTED", "PaymentIntent status is set to CANCELED"),
        ("PENDING", "PaymentIntent status is set to PENDING"),
        ("ERROR", "Unsupported PayNow payment_status"),
    ],
)
def test_bi_finish_tags_keep_their_wording(paynow_intent, monkeypatch, status, finish_tag):
    """BI reports group notifications by finish tag; the state rules must not rename them."""
    tags = []

    class RecordingEvent:
        def __init__(self, **kwargs):
            pass

        def finish_with_success(self, finish_tag, details=None):
            tags.append(finish_tag)

        def finish_with_error(self, finish_tag, details=None, error_message=None):
            tags.append(finish_tag)

    monkeypatch.setattr(paynow_views, "Checkout_PayNowNotifyEvent", RecordingEvent)

    _notify(status)

    assert tags == [finish_tag]
