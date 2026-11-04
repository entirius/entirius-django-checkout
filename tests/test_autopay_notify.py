# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Autopay ITNs: which ones may change a payment, and how far.

An ITN is verified with the non-empty ``AUTOPAY_HASH_KEY`` entry of the channel in the URL;
without one nothing is verified, so nothing passes. A verified ITN reaches only an Autopay payment
of that channel.

ITNs are hashed the way Autopay hashes them (the SDK's ``generate_hash`` over the XML fields).
"""

import base64
import xml.etree.ElementTree as ET
from urllib.parse import quote

import pytest
from autopay_sdk.utils.hash import generate_hash
from django.test import RequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider import autopay as autopay_views

factory = RequestFactory()

HASH_KEY = "synthetic-hash-key"
AUTOPAY_ORDER_ID = "AUTOPAY-ORDER-001"


@pytest.fixture(autouse=True)
def hash_key(monkeypatch, channel):
    monkeypatch.setattr(checkout_settings, "AUTOPAY_HASH_KEY", {channel.idx: HASH_KEY})


def _itn(channel, payment_status: str, key: str = HASH_KEY):
    fields = (
        f"<serviceID>100</serviceID><transactions><transaction><orderID>{AUTOPAY_ORDER_ID}</orderID>"
        "<remoteID>REMOTE-001</remoteID><amount>10.00</amount><currency>PLN</currency>"
        "<gatewayID>1</gatewayID><paymentDate>20260101120000</paymentDate>"
        f"<paymentStatus>{payment_status}</paymentStatus></transaction></transactions>"
    )
    unsigned = ET.fromstring(f"<transactionList>{fields}</transactionList>")  # noqa: S314 - own synthetic XML
    signature = generate_hash(
        channel=channel.idx, hash_key={channel.idx: key}, **autopay_views.extract_from_xml(unsigned)
    )
    xml = f"<transactionList>{fields}<hash>{signature}</hash></transactionList>"
    body = "transactions=" + quote(base64.b64encode(xml.encode()).decode())
    request = factory.post("/notify/autopay/", data=body, content_type="application/x-www-form-urlencoded")
    return autopay_views.autopay_return(request, channel_idx=channel.idx)


def _intent(channel, customer, payment_status=PaymentIntentStatus.PENDING, order_status=OrderStatus.UNPAID):
    method = PaymentMethod.objects.create(
        channel=channel, code="autopay", provider=PaymentProvider.AUTOPAY, name_t9n={"en": "Autopay"}
    )
    order = Order.objects.create(channel=channel, customer=customer, order_status=order_status, order_body={})
    return PaymentIntent.objects.create(
        order=order, method=method, code=method.code, external_order_id=AUTOPAY_ORDER_ID, payment_status=payment_status
    )


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


UNTOUCHED = (PaymentIntentStatus.PENDING, OrderStatus.UNPAID)


# --- The hash key ------------------------------------------------------------------------


@pytest.mark.django_db
def test_success_confirms_an_order_waiting_for_payment(channel, customer):
    payment_intent = _intent(channel, customer)

    response = _itn(channel, "SUCCESS")

    assert response.status_code == 200
    assert b"<confirmation>CONFIRMED</confirmation>" in response.content
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize("configured", [{"test-channel": ""}, {"test-channel": None}, {}, None])
def test_channel_without_a_hash_key_rejects(channel, customer, monkeypatch, configured):
    monkeypatch.setattr(checkout_settings, "AUTOPAY_HASH_KEY", configured)
    payment_intent = _intent(channel, customer)

    response = _itn(channel, "SUCCESS", key="")

    assert response.status_code == 400
    assert b"NOTCONFIRMED" in response.content
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_hash_that_is_not_ascii_is_rejected_not_an_error(channel, customer):
    payment_intent = _intent(channel, customer)
    xml = (
        f"<transactionList><serviceID>100</serviceID><transactions><transaction><orderID>{AUTOPAY_ORDER_ID}</orderID>"
        "<remoteID>REMOTE-001</remoteID><amount>10.00</amount><currency>PLN</currency>"
        "<paymentStatus>SUCCESS</paymentStatus></transaction></transactions><hash>zażółć</hash></transactionList>"
    )
    body = "transactions=" + quote(base64.b64encode(xml.encode()).decode())
    request = factory.post("/notify/autopay/", data=body, content_type="application/x-www-form-urlencoded")

    response = autopay_views.autopay_return(request, channel_idx=channel.idx)

    assert response.status_code == 400
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_itn_hashed_with_another_key_is_rejected(channel, customer):
    payment_intent = _intent(channel, customer)

    response = _itn(channel, "SUCCESS", key="synthetic-other-key")

    assert response.status_code == 400
    assert _state(payment_intent) == UNTOUCHED


# --- The hash key belongs to one channel and one gateway ------------------------------------


@pytest.mark.django_db
def test_itn_hashed_for_one_channel_cannot_confirm_another_channels_order(channel, other_channel, customer):
    """The key comes from the channel in the URL; the orderID must not reach a payment of another channel."""
    payment_intent = _intent(other_channel, customer)

    response = _itn(channel, "SUCCESS")

    assert response.status_code != 200
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_itn_cannot_confirm_another_gateways_payment(channel, customer):
    method = PaymentMethod.objects.create(
        channel=channel, code="payu", provider=PaymentProvider.PAYU, name_t9n={"en": "PayU"}
    )
    order = Order.objects.create(channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={})
    payment_intent = PaymentIntent.objects.create(
        order=order,
        method=method,
        code="payu",
        external_order_id=AUTOPAY_ORDER_ID,
        payment_status=PaymentIntentStatus.PENDING,
    )

    response = _itn(channel, "SUCCESS")

    assert response.status_code != 200
    assert _state(payment_intent) == UNTOUCHED
