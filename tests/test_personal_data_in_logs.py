# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Log lines and BI events carry no personal data of a customer.

They did: the admin order preview logged the billing e-mail; the PayU, PayPal and Stripe notification views logged
(and sent to BI) the whole notification, buyer or payer included; the Autopay ITN view logged its XML, whose
``customerData`` is the buyer; the v1 order view logged ``str(customer)``, the username, which is the e-mail.
Gateway payloads now reach a log line only through ``scrub_gateway_payload`` (ids, amounts, statuses).

Every personal value below is invented.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import quote

import pytest
from autopay_sdk.utils.hash import generate_hash
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import RefreshToken

from django_checkout import settings as checkout_settings
from django_checkout.api.v2.views.admin.order_views import AdminOrderDetailView
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views import order as order_views
from django_checkout.views.payment_provider import autopay as autopay_views
from django_checkout.views.payment_provider import paypal as paypal_views
from django_checkout.views.payment_provider import payu as payu_views
from django_checkout.views.payment_provider import stripe as stripe_views

EMAIL = "log.kupujaca@example.org"
FIRST, LAST = "Testina", "Logowska"
PHONE = "600100299"
STREET = "ul. Dziennikowa 3"
PERSONAL = (EMAIL, FIRST, LAST, PHONE, STREET)
BUYER = {"email": EMAIL, "phone": PHONE, "firstName": FIRST, "lastName": LAST}

factory = RequestFactory()


@pytest.fixture
def bi_details(monkeypatch):
    """Patch a view module's BI event class; returns the ``details`` every finish received."""
    received = []

    class RecordingEvent:
        def __init__(self, **kwargs):
            pass

        def finish_with_success(self, finish_tag, details=None):
            received.append(details)

        def finish_with_error(self, finish_tag, details=None, error_message=None):
            received.append(details)

        def finish_with_exception(self, details=None, e=None):
            received.append(details)

    def patch(module, name):
        monkeypatch.setattr(module, name, RecordingEvent)
        return received

    return patch


def _leaks(caplog, events=()) -> list[str]:
    records = [{**vars(record), "message": record.getMessage()} for record in caplog.records]
    dumped = json.dumps([records, list(events)], default=str)
    return [value for value in PERSONAL if value in dumped]


def _post_json(path: str, body: dict, **meta):
    return factory.post(path, data=json.dumps(body), content_type="application/json", **meta)


# --- Gateway notifications --------------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"order": {"extOrderId": "25-000001", "buyer": BUYER}},  # no orderId
        {"order": {"orderId": "PAYU-UNKNOWN", "status": "COMPLETED", "buyer": BUYER}},  # no such payment
    ],
    ids=["no-order-id", "unknown-payment"],
)
def test_payu_notification_logs_no_buyer_data(caplog, bi_details, body):
    events = bi_details(payu_views, "Checkout_PayuNotifyEvent")

    payu_views.payu_notify(_post_json("/notify/payu/", body))

    assert events
    assert _leaks(caplog, events) == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        {"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"payer": {"email_address": EMAIL}}},  # no id
        {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": "PAYPAL-UNKNOWN", "payer": BUYER}},
    ],
    ids=["no-id", "unknown-payment"],
)
def test_paypal_notification_logs_no_payer_data(caplog, bi_details, body):
    events = bi_details(paypal_views, "Checkout_PayPalNotifyEvent")

    paypal_views.paypal_notify(_post_json("/notify/paypal/", body))

    assert events
    assert _leaks(caplog, events) == []


STRIPE_SECRET = "synthetic-endpoint-secret"


def _stripe_event() -> str:
    session = {
        "id": "SESSION-UNKNOWN",
        "object": "checkout.session",
        "payment_status": "paid",
        "customer_details": {"email": EMAIL, "name": f"{FIRST} {LAST}", "phone": PHONE},
    }
    return json.dumps(
        {"id": "EVT-1", "object": "event", "type": "checkout.session.completed", "data": {"object": session}}
    )


def _stripe_request(payload: str, secret: str):
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f"{timestamp}.{payload}".encode(), hashlib.sha256).hexdigest()
    return factory.post(
        "/notify/stripe/",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}",
    )


@pytest.mark.django_db
@pytest.mark.parametrize("secret", [STRIPE_SECRET, "another-secret"], ids=["unknown-payment", "bad-signature"])
def test_stripe_notification_logs_no_customer_details(channel, caplog, bi_details, monkeypatch, secret):
    monkeypatch.setattr(stripe_views.stripe, "api_key", None)
    PaymentMethod.objects.create(
        channel=channel,
        code="stripe",
        provider=PaymentProvider.STRIPE,
        name_t9n={"en": "Stripe"},
        additional_data={"api_key": "synthetic-api-key", "endpoint_secret": STRIPE_SECRET},
    )
    events = bi_details(stripe_views, "Checkout_StripeNotifyEvent")

    stripe_views.stripe_notify(_stripe_request(_stripe_event(), secret), channel_idx=channel.idx)

    assert events
    assert _leaks(caplog, events) == []


AUTOPAY_KEY = "synthetic-hash-key"
AUTOPAY_ORDER_ID = "AUTOPAY-LOG-1"


def _autopay_itn(channel):
    fields = (
        f"<serviceID>100</serviceID><transactions><transaction><orderID>{AUTOPAY_ORDER_ID}</orderID>"
        "<remoteID>REMOTE-1</remoteID><amount>10.00</amount><currency>PLN</currency>"
        "<gatewayID>1</gatewayID><paymentDate>20260101120000</paymentDate><paymentStatus>SUCCESS</paymentStatus>"
        f"<customerData><fName>{FIRST}</fName><lName>{LAST}</lName><streetName>{STREET}</streetName>"
        f"<senderData>{FIRST} {LAST}</senderData></customerData></transaction></transactions>"
    )
    unsigned = ET.fromstring(f"<transactionList>{fields}</transactionList>")  # noqa: S314 - own synthetic XML
    signature = generate_hash(
        channel=channel.idx, hash_key={channel.idx: AUTOPAY_KEY}, **autopay_views.extract_from_xml(unsigned)
    )
    xml = f"<transactionList>{fields}<hash>{signature}</hash></transactionList>"
    body = "transactions=" + quote(base64.b64encode(xml.encode()).decode())
    request = factory.post("/notify/autopay/", data=body, content_type="application/x-www-form-urlencoded")
    return autopay_views.autopay_return(request, channel_idx=channel.idx)


@pytest.mark.django_db
@pytest.mark.parametrize("has_payment", [True, False], ids=["confirmed", "unknown-payment"])
def test_autopay_itn_logs_no_customer_data(channel, customer, caplog, bi_details, monkeypatch, has_payment):
    monkeypatch.setattr(checkout_settings, "AUTOPAY_HASH_KEY", {channel.idx: AUTOPAY_KEY})
    if has_payment:
        method = PaymentMethod.objects.create(
            channel=channel, code="autopay", provider=PaymentProvider.AUTOPAY, name_t9n={"en": "Autopay"}
        )
        order = Order.objects.create(channel=channel, customer=customer, order_body={})
        PaymentIntent.objects.create(
            order=order,
            method=method,
            code=method.code,
            external_order_id=AUTOPAY_ORDER_ID,
            payment_status=PaymentIntentStatus.PENDING,
        )
    events = bi_details(autopay_views, "Checkout_AutopayReturnEvent")

    _autopay_itn(channel)

    assert events
    assert _leaks(caplog, events) == []


# --- Admin preview and v1 orders --------------------------------------------------------------------------------


@pytest.mark.django_db
def test_admin_order_preview_logs_the_order_not_the_email(channel, admin_user, api_key, caplog):
    order = Order.objects.create(
        channel=channel, order_status=OrderStatus.CONFIRMED, order_body={}, billing_email=EMAIL, shipping_email=EMAIL
    )
    request = APIRequestFactory().get("/admin/orders/x/", HTTP_X_API_KEY=api_key.key)
    force_authenticate(request, user=admin_user)

    with caplog.at_level("INFO", logger="checkout.v2.admin"):
        response = AdminOrderDetailView.as_view()(request, channel_idx=channel.idx, uid=order.pretty_id)

    assert response.status_code == 200
    assert any(str(order.order_id) in record.getMessage() for record in caplog.records)
    assert _leaks(caplog) == []


@pytest.mark.django_db
def test_v1_order_failure_logs_the_customer_pk_not_the_username(channel, api_key, caplog, bi_details):
    """accounts registers a customer with the e-mail as the username, and ``str(customer)`` is the username."""
    from django_accounts.models import Customer

    user = get_user_model().objects.create_user(username=EMAIL, email=EMAIL, password="synthetic-pass-1")
    Customer.objects.create(user=user)
    token = str(RefreshToken.for_user(user).access_token)
    events = bi_details(order_views, "Checkout_OrderCreationEvent")
    request = _post_json(
        "/orders/", {"cart_id": str(uuid.uuid4())}, HTTP_X_API_KEY=api_key.key, HTTP_AUTHORIZATION=f"Bearer {token}"
    )

    response = order_views.order_view(request, channel_idx=channel.idx)

    assert response.status_code == 400
    assert events
    assert _leaks(caplog, events) == []
