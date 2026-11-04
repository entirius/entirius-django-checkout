# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Przelewy24 notifications: which ones may change a payment, and how far.

A notification reaches only a Przelewy24 payment, and is verified with that payment's own method.
Its ``sign`` (SHA-384 over the documented fields and the CRC key) must match before Przelewy24 is
asked to verify the transaction. A verified payment completes the order only while it waits for
payment (``domain/payment_provider/notification_state.py``).

Przelewy24 is never called: the provider's ``verify_transaction_payment`` is replaced, and any
other outbound HTTP fails the test.
"""

import hashlib
import json

import pytest
import requests
from django.test import RequestFactory

from django_checkout.domain.payment_provider.przelewy24_payment_provider import Przelewy24PaymentProvider
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider import przelewy24 as przelewy24_views

factory = RequestFactory()

SESSION_ID = "SESSION-001"
CRC_KEY = "synthetic-crc-key"
SIGNED_FIELDS = (
    "merchantId",
    "posId",
    "sessionId",
    "amount",
    "originAmount",
    "currency",
    "orderId",
    "methodId",
    "statement",
)


def _sign(body: dict, crc_key: str = CRC_KEY) -> str:
    """Przelewy24's documented formula, written out independently of the module under test."""
    payload = {field: body[field] for field in SIGNED_FIELDS} | {"crc": crc_key}
    return hashlib.sha384(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.fixture(autouse=True)
def order_methods(monkeypatch):
    """An order lists its payment methods in its body; these orders list their intents' methods, in order."""
    monkeypatch.setattr(
        Order,
        "selected_payment_methods",
        property(lambda order: [pi.method for pi in order.payment_items.order_by("pk")]),
    )


@pytest.fixture(autouse=True)
def verifications(monkeypatch):
    """Notification bodies sent to Przelewy24's verify API; it confirms each of them."""
    sent = []

    def verify(self, body):
        sent.append(body)
        return {"data": {"status": "success"}, "responseCode": 0}

    def no_network(session, method, url, **kwargs):
        raise AssertionError(f"Unexpected outbound call in a test: {method} {url}")

    monkeypatch.setattr(Przelewy24PaymentProvider, "verify_transaction_payment", verify)
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    return sent


def _notify(sign_with=CRC_KEY, **overrides):
    body = {
        "merchantId": 1,
        "posId": 1,
        "sessionId": SESSION_ID,
        "amount": 1000,
        "originAmount": 1000,
        "currency": "PLN",
        "orderId": 123456,
        "methodId": 1,
        "statement": "p24-SYNTHETIC",
    }
    body.update(overrides)
    body = {key: value for key, value in body.items() if value is not None}
    if sign_with is not None and "sign" not in body and all(field in body for field in SIGNED_FIELDS):
        body["sign"] = _sign(body, sign_with)
    request = factory.post("/notify/przelewy24/", data=json.dumps(body), content_type="application/json")
    return przelewy24_views.przelewy24_notify(request)


def _method(channel, provider=PaymentProvider.PRZELEWY24, crc_key=CRC_KEY, code=None):
    return PaymentMethod.objects.create(
        channel=channel,
        code=code or str(provider),
        provider=provider,
        name_t9n={"en": str(provider)},
        additional_data={"client_id": "1", "client_secret": "x", "crc_key": crc_key},
    )


def _intent(
    channel,
    customer,
    provider=PaymentProvider.PRZELEWY24,
    payment_status=PaymentIntentStatus.PENDING,
    order_status=OrderStatus.UNPAID,
    crc_key=CRC_KEY,
    order=None,
):
    method = _method(channel, provider, crc_key)
    order = order or Order.objects.create(channel=channel, customer=customer, order_status=order_status, order_body={})
    return PaymentIntent.objects.create(
        order=order, method=method, code=method.code, external_order_id=SESSION_ID, payment_status=payment_status
    )


def _state(payment_intent):
    payment_intent.refresh_from_db()
    payment_intent.order.refresh_from_db()
    return payment_intent.payment_status, payment_intent.order.order_status


UNTOUCHED = (PaymentIntentStatus.PENDING, OrderStatus.UNPAID)


@pytest.mark.django_db
def test_paid_notification_confirms_an_order_waiting_for_payment(channel, customer, verifications):
    payment_intent = _intent(channel, customer)

    response = _notify()

    assert response.status_code == 200
    assert len(verifications) == 1
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


@pytest.mark.django_db
@pytest.mark.parametrize("provider", [PaymentProvider.PAYU, PaymentProvider.STRIPE])
def test_notification_cannot_touch_another_gateways_payment(channel, customer, verifications, provider):
    payment_intent = _intent(channel, customer, provider=provider)

    response = _notify()

    assert response.status_code == 403
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_body_without_session_id_is_rejected_without_asking_przelewy24(channel, customer, verifications):
    payment_intent = _intent(channel, customer)

    response = _notify(sessionId=None)

    assert response.status_code == 500
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_verification_uses_the_payments_own_method(channel, customer, verifications):
    """With a voucher listed first on the order, the order's first method is not the one Przelewy24 paid."""
    order = Order.objects.create(channel=channel, customer=customer, order_status=OrderStatus.UNPAID, order_body={})
    voucher = _method(channel, PaymentProvider.VOUCHER)
    PaymentIntent.objects.create(
        order=order, method=voucher, code=voucher.code, external_order_id="VOUCHER-001", payment_status="complete"
    )
    payment_intent = _intent(channel, customer, order=order)

    response = _notify()

    assert response.status_code == 200
    assert len(verifications) == 1
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


# --- The sign -----------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "notify_kwargs",
    [{"sign_with": "synthetic-other-key"}, {"sign_with": None}, {"sign": "0" * 96}, {"sign": 7}],
    ids=["wrong-key", "no-sign", "made-up-sign", "not-a-string"],
)
def test_unsigned_or_wrongly_signed_notification_is_rejected_before_asking_przelewy24(
    channel, customer, verifications, notify_kwargs
):
    payment_intent = _intent(channel, customer)

    response = _notify(**notify_kwargs)

    assert response.status_code == 403
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_amount_changed_after_signing_is_rejected(channel, customer, verifications):
    payment_intent = _intent(channel, customer)
    signed = {
        "merchantId": 1,
        "posId": 1,
        "sessionId": SESSION_ID,
        "amount": 1000,
        "originAmount": 1000,
        "currency": "PLN",
        "orderId": 123456,
        "methodId": 1,
        "statement": "p24-SYNTHETIC",
    }

    response = _notify(sign=_sign(signed), amount=1)

    assert response.status_code == 403
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
@pytest.mark.parametrize("crc_key", ["", None])
def test_method_without_a_crc_key_rejects_instead_of_trusting(channel, customer, verifications, crc_key):
    payment_intent = _intent(channel, customer, crc_key=crc_key)

    response = _notify(sign_with="")

    assert response.status_code == 403
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_body_missing_a_signed_field_is_rejected(channel, customer, verifications):
    payment_intent = _intent(channel, customer)

    response = _notify(sign="0" * 96, methodId=None)

    assert response.status_code == 403
    assert verifications == []
    assert _state(payment_intent) == UNTOUCHED


@pytest.mark.django_db
def test_statement_with_spaces_and_diacritics_is_signed_as_written(channel, customer, verifications):
    """The SDK's own sign helpers strip spaces; Przelewy24 signs the statement as it sends it."""
    payment_intent = _intent(channel, customer)

    response = _notify(statement="Zamówienie nr 1 - łódź")

    assert response.status_code == 200
    assert len(verifications) == 1
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.CONFIRMED)


# --- A resent notification confirms nothing twice -----------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("order_status", [OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED, OrderStatus.RETURNED])
def test_resent_notification_leaves_an_order_that_moved_on(channel, customer, order_status):
    payment_intent = _intent(channel, customer, payment_status=PaymentIntentStatus.COMPLETE, order_status=order_status)

    response = _notify()

    assert response.status_code == 200
    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, order_status)


@pytest.mark.django_db
def test_notification_keeps_an_order_moved_on_before_the_payment_was_recorded(channel, customer):
    payment_intent = _intent(channel, customer, order_status=OrderStatus.IN_PROGRESS)

    _notify()

    assert _state(payment_intent) == (PaymentIntentStatus.COMPLETE, OrderStatus.IN_PROGRESS)


@pytest.mark.django_db
def test_bi_finish_tag_keeps_its_wording(channel, customer, monkeypatch):
    """BI reports group notifications by finish tag; the state rules must not rename it."""
    tags = []

    class RecordingEvent:
        def __init__(self, **kwargs):
            pass

        def finish_with_success(self, finish_tag, details=None):
            tags.append(finish_tag)

        def finish_with_error(self, finish_tag, details=None, error_message=None):
            tags.append(finish_tag)

    monkeypatch.setattr(przelewy24_views, "Checkout_Przelewy24NotifyEvent", RecordingEvent)
    _intent(channel, customer)

    _notify()

    assert tags == ["PaymentIntent status completed"]


# --- What is stored ---------------------------------------------------------------------


@pytest.mark.django_db
def test_stored_notification_keeps_ids_and_amounts_only(channel, customer, verifications):
    """The sign is checked, and Przelewy24 asked, with the raw body; the payment keeps the allowlisted keys."""
    payment_intent = _intent(channel, customer)

    response = _notify()

    assert response.status_code == 200
    assert "sign" in verifications[0]
    payment_intent.refresh_from_db()
    assert payment_intent.provider_notify == {
        "merchantId": 1,
        "posId": 1,
        "sessionId": SESSION_ID,
        "amount": 1000,
        "originAmount": 1000,
        "currency": "PLN",
        "orderId": 123456,
        "methodId": 1,
    }
