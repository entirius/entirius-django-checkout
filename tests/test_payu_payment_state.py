# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A PayU status only moves a payment forward.

PayU redelivers notifications and does not keep their order. A signed status is applied through
``domain/payment_provider/notification_state.py``: a COMPLETE payment is final, and an order is
confirmed or cancelled (its stock released) only while it waits for payment.
"""

import hashlib
import json

import pytest
from django.test import RequestFactory

from django_checkout.domain.payment_provider import notification_state
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider import payu as payu_views

factory = RequestFactory()

SECOND_KEY = "synthetic-second-key"
PAYU_ORDER_ID = "PAYU-ORDER-001"


def _notify(status: str):
    body = json.dumps({"order": {"orderId": PAYU_ORDER_ID, "status": status}}).encode()
    signature = hashlib.md5(body + SECOND_KEY.encode()).hexdigest()  # noqa: S324 - PayU's protocol
    header = f"sender=checkout;signature={signature};algorithm=MD5;content=DOCUMENT"
    request = factory.post("/notify/payu/", data=body, content_type="application/json", HTTP_OPENPAYU_SIGNATURE=header)
    return payu_views.payu_notify(request)


@pytest.fixture
def payu_method(channel):
    return PaymentMethod.objects.create(
        channel=channel,
        code="payu",
        provider=PaymentProvider.PAYU,
        name_t9n={"en": "PayU"},
        additional_data={"pos_id": "1", "client_id": "1", "client_secret": "x", "second_key": SECOND_KEY},
    )


def _intent(channel, customer, method, payment_status=PaymentIntentStatus.PENDING, order_status=OrderStatus.UNPAID):
    order = Order.objects.create(channel=channel, customer=customer, order_status=order_status, order_body={})
    return PaymentIntent.objects.create(
        order=order, method=method, code=method.code, external_order_id=PAYU_ORDER_ID, payment_status=payment_status
    )


@pytest.fixture
def released(monkeypatch):
    """Orders whose stock reservation was released."""
    orders = []
    monkeypatch.setattr(notification_state, "release_stock_reservation", orders.append)
    # The handler released stock itself before it went through notification_state.
    monkeypatch.setattr(payu_views, "release_stock_reservation", orders.append, raising=False)
    return orders


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["PENDING", "CANCELED", "WAITING_FOR_CONFIRMATION"])
def test_late_status_leaves_a_completed_payment_alone(channel, customer, payu_method, released, status):
    payment_intent = _intent(channel, customer, payu_method, PaymentIntentStatus.COMPLETE, OrderStatus.IN_PROGRESS)

    response = _notify(status)

    assert response.status_code == 200
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.IN_PROGRESS)
    assert released == []


@pytest.mark.django_db
def test_canceled_leaves_an_order_confirmed_by_hand(channel, customer, payu_method, released):
    payment_intent = _intent(channel, customer, payu_method, order_status=OrderStatus.CONFIRMED)

    _notify("CANCELED")

    assert _state(payment_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CONFIRMED)
    assert released == []


@pytest.mark.django_db
def test_canceled_cancels_an_order_still_waiting_and_releases_its_stock(channel, customer, payu_method, released):
    payment_intent = _intent(channel, customer, payu_method)

    _notify("CANCELED")

    assert _state(payment_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)
    assert [order.pk for order in released] == [payment_intent.order_id]


@pytest.mark.django_db
def test_repeated_canceled_releases_the_stock_once(channel, customer, payu_method, released):
    payment_intent = _intent(channel, customer, payu_method)

    _notify("CANCELED")
    _notify("CANCELED")

    assert _state(payment_intent) == (PaymentIntentStatus.CANCELLED, OrderStatus.CANCELED)
    assert len(released) == 1


@pytest.mark.django_db
def test_completed_keeps_an_order_that_moved_on(channel, customer, payu_method):
    payment_intent = _intent(channel, customer, payu_method, order_status=OrderStatus.IN_PROGRESS)

    _notify("COMPLETED")

    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.IN_PROGRESS)


@pytest.mark.django_db
def test_completed_confirms_an_order_waiting_for_payment(channel, customer, payu_method):
    payment_intent = _intent(channel, customer, payu_method)

    _notify("COMPLETED")

    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status,finish_tag",
    [
        ("COMPLETED", "PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED"),
        ("CANCELED", "PaymentIntent status is set to CANCELED"),
        ("PENDING", "PaymentIntent status is set to PENDING"),
        ("WAITING_FOR_CONFIRMATION", "Unsupported PayU payment_status"),
    ],
)
def test_bi_finish_tags_keep_their_wording(channel, customer, payu_method, released, monkeypatch, status, finish_tag):
    """BI reports group notifications by finish tag; the state rules must not rename them."""
    tags = []

    class RecordingEvent:
        def __init__(self, **kwargs):
            pass

        def finish_with_success(self, finish_tag, details=None):
            tags.append(finish_tag)

        def finish_with_error(self, finish_tag, details=None, error_message=None):
            tags.append(finish_tag)

    monkeypatch.setattr(payu_views, "Checkout_PayuNotifyEvent", RecordingEvent)
    _intent(channel, customer, payu_method)

    _notify(status)

    assert tags == [finish_tag]
