# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Gateway payloads are stored through the allowlist, always.

``PaymentIntent.provider_request`` and ``provider_notify`` were stored whole: the buyer's name, e-mail, phone,
address and IP, PayPal's payer, Stripe's ``customer_details``, the BLIK code and the card token. Only an erasure
reduced them to ids, amounts, currencies, statuses and timestamps, and a notification that came after the erasure
stored the buyer again. ``PaymentIntent.save()`` now scrubs them, whoever writes them.

The notification side of each provider is tested with its view (``test_payu_notify_signature``, ``test_paypal``,
``test_stripe_notify``, ``test_przelewy24_notify``, ``test_paynow_notify_signature``); this module covers what
``process_payment`` stores for every provider class, the model itself and a notification after an erasure.

Every personal value below is invented.
"""

import hashlib
import json
from dataclasses import dataclass, field
from io import StringIO
from types import SimpleNamespace

import pytest
from autopay_sdk.dto.order import Order as AutopayOrder
from django.core.management import call_command
from django.test import RequestFactory

from django_checkout.domain.payment_provider.autopay_payment_provider import AutopayPaymentProvider
from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.domain.payment_provider.paynow_payment_provider import PayNowPaymentProvider
from django_checkout.domain.payment_provider.paypal_payment_provider import PayPalPaymentProvider
from django_checkout.domain.payment_provider.payu_blik_payment_provider import PayUBlikPaymentProvider
from django_checkout.domain.payment_provider.payu_card_payment_provider import PayUCardPaymentProvider
from django_checkout.domain.payment_provider.payu_payment_provider import PayUPaymentProvider
from django_checkout.domain.payment_provider.przelewy24_payment_provider import Przelewy24PaymentProvider
from django_checkout.domain.payment_provider.stripe_payment_provider import StripePaymentProvider
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod
from django_checkout.views.payment_provider.payu import payu_notify

EMAIL = "zapis.kupujacy@example.org"
FIRST, LAST = "Placida", "Zapisowa"
PHONE = "600100288"
STREET, CITY, POSTCODE = "ul. Bramkowa 9", "Testowo", "00-951"
IP = "192.0.2.21"
BLIK_CODE = "777321"
CARD_TOKEN = "TOK_SYNTHETIC_CARD_1"
PERSONAL = (EMAIL, FIRST, LAST, PHONE, STREET, CITY, POSTCODE, IP, BLIK_CODE, CARD_TOKEN)


def _leftovers(*payloads) -> list[str]:
    dumped = json.dumps(payloads)
    return [value for value in PERSONAL if value in dumped]


def _at(payload, *path):
    for step in path:
        payload = payload[step]
    return payload


# --- What process_payment stores, per provider ------------------------------------------------------------------


@dataclass
class _PayUOrder:
    """The shape of the SDK's PayU order DTO, as ``asdict`` stores it."""

    extOrderId: str = "25-000001"
    totalAmount: str = "12300"
    currencyCode: str = "PLN"
    merchantPosId: str = "300746"
    customerIp: str = IP
    description: str = f"Order of {FIRST} {LAST}"
    continueUrl: str = "https://shop.example.com/thanks"
    buyer: dict = field(default_factory=lambda: {"email": EMAIL, "phone": PHONE, "firstName": FIRST, "lastName": LAST})
    products: list = field(default_factory=lambda: [{"name": "Mug", "unitPrice": "12300", "quantity": "1"}])
    payMethods: dict = field(default_factory=dict)


@dataclass
class _Przelewy24Transaction:
    merchantId: int = 1
    posId: int = 1
    sessionId: str = "SESSION-REQ-1"
    amount: int = 12300
    currency: str = "PLN"
    description: str = f"Order of {FIRST} {LAST}"
    email: str = EMAIL
    client: str = f"{FIRST} {LAST}"
    address: str = STREET
    zip: str = POSTCODE
    city: str = CITY
    phone: str = PHONE
    urlReturn: str = "https://shop.example.com/thanks"
    sign: str = "synthetic-sign"


def _dto(payload: dict) -> SimpleNamespace:
    """An SDK DTO that ``process_payment`` stores through ``to_dict()``."""
    return SimpleNamespace(to_dict=lambda: payload)


def _payu(pay_method):
    return lambda: (_PayUOrder(payMethods={"payMethod": pay_method}), None)


PAYPAL_REQUEST = {
    "intent": "CAPTURE",
    "payer": {"email_address": EMAIL, "name": {"given_name": FIRST, "surname": LAST}},
    "purchase_units": [
        {
            "reference_id": "25-000001",
            "amount": {"currency_code": "PLN", "value": "123.00"},
            "shipping": {"name": {"full_name": f"{FIRST} {LAST}"}, "address": {"address_line_1": STREET}},
        }
    ],
}
PAYPAL_CREATED = {
    "id": "PAYPAL-ORDER-REQ-1",
    "status": "PAYER_ACTION_REQUIRED",
    "payment_source": {"paypal": {"email_address": EMAIL, "name": {"given_name": FIRST, "surname": LAST}}},
    "links": [{"href": "https://www.sandbox.paypal.com/checkoutnow?token=X", "rel": "payer-action"}],
}
STRIPE_REQUEST = {
    "mode": "payment",
    "customer_email": EMAIL,
    "client_reference_id": "25-000001",
    "success_url": "https://shop.example.com/thanks",
    "line_items": [{"price_data": {"currency": "pln", "unit_amount": 12300, "product_data": {"name": "Mug"}}}],
    "metadata": {"buyer": f"{FIRST} {LAST}"},
}
PAYNOW_REQUEST = {
    "amount": 12300,
    "currency": "PLN",
    "externalId": "25-000001",
    "description": "Shop",
    "continueUrl": "https://shop.example.com/thanks",
    "buyer": {
        "email": EMAIL,
        "firstName": FIRST,
        "lastName": LAST,
        "phone": {"prefix": "+48", "number": PHONE},
        "address": {"billing": {"street": STREET, "city": CITY, "zipcode": POSTCODE}},
    },
}

# provider class, PaymentProvider, () -> (provider_request, provider_notify), kept (which, path, value)
PROVIDERS = {
    "payu": (
        PayUPaymentProvider,
        PaymentProvider.PAYU,
        _payu({"type": "PBL", "value": "m"}),
        [("request", ("extOrderId",), "25-000001"), ("request", ("totalAmount",), "12300")],
    ),
    "payu_card": (
        PayUCardPaymentProvider,
        PaymentProvider.PAYU_CARD,
        _payu({"type": "CARD_TOKEN", "value": CARD_TOKEN}),
        [("request", ("extOrderId",), "25-000001"), ("request", ("currencyCode",), "PLN")],
    ),
    "payu_blik": (
        PayUBlikPaymentProvider,
        PaymentProvider.PAYU_BLIK,
        _payu({"type": "BLIK_AUTHORIZATION_CODE", "value": BLIK_CODE}),
        [("request", ("extOrderId",), "25-000001"), ("request", ("totalAmount",), "12300")],
    ),
    "paypal": (
        PayPalPaymentProvider,
        PaymentProvider.PAYPAL,
        lambda: (_dto(PAYPAL_REQUEST), PAYPAL_CREATED),
        [
            ("request", ("purchase_units", 0, "amount", "value"), "123.00"),
            ("request", ("purchase_units", 0, "reference_id"), "25-000001"),
            ("notify", ("id",), "PAYPAL-ORDER-REQ-1"),
        ],
    ),
    "stripe": (
        StripePaymentProvider,
        PaymentProvider.STRIPE,
        lambda: (STRIPE_REQUEST, None),
        [("request", ("client_reference_id",), "25-000001")],
    ),
    "przelewy24": (
        Przelewy24PaymentProvider,
        PaymentProvider.PRZELEWY24,
        lambda: (_Przelewy24Transaction(), {"data": {"token": "P24-TOKEN"}, "responseCode": 0}),
        [("request", ("sessionId",), "SESSION-REQ-1"), ("request", ("amount",), 12300)],
    ),
    "paynow": (
        PayNowPaymentProvider,
        PaymentProvider.PAYNOW,
        lambda: (_dto(PAYNOW_REQUEST), None),
        [("request", ("externalId",), "25-000001"), ("request", ("amount",), 12300)],
    ),
    "autopay": (
        AutopayPaymentProvider,
        PaymentProvider.AUTOPAY,
        lambda: (
            AutopayOrder(
                ServiceID="100",
                OrderID="25-000001",
                Amount="123.00",
                Hash="synthetic-hash",
                Description=f"{FIRST} {LAST}",
                CustomerEmail=EMAIL,
                CustomerIP=IP,
            ),
            {"order_id": "AUTOPAY-REQ-1", "redirect_url": "https://pay.example.com/r/1"},
        ),
        [
            ("request", ("OrderID",), "25-000001"),
            ("request", ("Amount",), "123.00"),
            ("notify", ("order_id",), "AUTOPAY-REQ-1"),
        ],
    ),
    # Any other provider (cash on delivery, the voucher module's) stores through the same save().
    "other": (
        BasePaymentProvider,
        PaymentProvider.COD,
        lambda: ({"voucher": {"id": "VOUCHER-1", "owner_email": EMAIL}, "amount": "10.00"}, None),
        [("request", ("voucher", "id"), "VOUCHER-1"), ("request", ("amount",), "10.00")],
    ),
}


def _intent(channel, provider: str, code: str | None = None) -> PaymentIntent:
    method = PaymentMethod.objects.create(
        channel=channel, code=code or provider, provider=provider, name_t9n={"en": provider}, additional_data={}
    )
    order = Order.objects.create(channel=channel, order_status=OrderStatus.UNPAID, order_body={})
    return PaymentIntent.objects.create(order=order, method=method, code=method.code)


@pytest.mark.django_db
@pytest.mark.parametrize("name", PROVIDERS)
def test_process_payment_stores_no_buyer_data(channel, name):
    provider_cls, provider_code, payloads, kept = PROVIDERS[name]
    intent = _intent(channel, provider_code, code=name)
    provider = provider_cls(intent.method)
    provider.provider_request, provider.provider_notify = payloads()
    provider.order_id = "GATEWAY-ORDER-1"

    provider.process_payment(intent)

    intent.refresh_from_db()
    assert _leftovers(intent.provider_request, intent.provider_notify) == []
    stored = {"request": intent.provider_request, "notify": intent.provider_notify}
    for which, path, value in kept:
        assert _at(stored[which], *path) == value, (which, path)
    assert intent.external_order_id == "GATEWAY-ORDER-1"


# --- The model --------------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_every_save_stores_the_payloads_through_the_allowlist(channel):
    intent = _intent(channel, PaymentProvider.PAYU)
    intent.provider_notify = {"order": {"orderId": "PAYU-1", "status": "COMPLETED", "buyer": {"email": EMAIL}}}
    intent.payment_status = PaymentIntentStatus.COMPLETE

    intent.save(update_fields=["provider_notify", "payment_status"])

    intent.refresh_from_db()
    assert intent.provider_notify == {"order": {"orderId": "PAYU-1", "status": "COMPLETED"}}


@pytest.mark.django_db
def test_a_save_that_does_not_write_the_payloads_leaves_them_alone(channel):
    intent = _intent(channel, PaymentProvider.PAYU)
    raw = {"order": {"orderId": "PAYU-2", "buyer": {"email": EMAIL}}}
    intent.provider_notify = raw
    intent.amount = "10.00"

    intent.save(update_fields=["amount"])

    assert intent.provider_notify == raw  # not written, so not rewritten on the instance either
    intent.refresh_from_db()
    assert intent.provider_notify is None


@pytest.mark.django_db
def test_anonymize_reports_nothing_for_payloads_already_reduced(channel):
    intent = _intent(channel, PaymentProvider.PAYU)
    intent.provider_notify = {"order": {"orderId": "PAYU-3", "buyer": {"email": EMAIL}}}
    intent.save()

    assert intent.anonymize() is False


@pytest.mark.django_db
def test_a_payload_that_is_no_json_object_is_not_stored(channel):
    intent = _intent(channel, PaymentProvider.AUTOPAY)
    intent.provider_notify = f"<transaction><email>{EMAIL}</email></transaction>"

    intent.save()

    intent.refresh_from_db()
    assert intent.provider_notify is None


# --- A notification after the erasure ---------------------------------------------------------------------------

SECOND_KEY = "synthetic-second-key"  # noqa: S105 - synthetic fixture


@pytest.mark.django_db
def test_a_late_notification_after_the_erasure_brings_no_buyer_data_back(channel):
    """PayU redelivers for days; the erased order's payment must not get the buyer back from it.

    A cancelled order may be erased while its payment is still open; PayU then reports the payment cancelled.
    """
    method = PaymentMethod.objects.create(
        channel=channel,
        code="payu",
        provider=PaymentProvider.PAYU,
        name_t9n={"en": "PayU"},
        additional_data={"pos_id": "1", "client_id": "1", "client_secret": "x", "second_key": SECOND_KEY},
    )
    order = Order.objects.create(channel=channel, order_status=OrderStatus.CANCELED, order_body={})
    intent = PaymentIntent.objects.create(
        order=order,
        method=method,
        code="payu",
        external_order_id="PAYU-LATE-1",
        payment_status=PaymentIntentStatus.PENDING,
    )
    order.anonymize()
    buyer = {"email": EMAIL, "phone": PHONE, "firstName": FIRST, "lastName": LAST}
    body = json.dumps({"order": {"orderId": "PAYU-LATE-1", "status": "CANCELED", "buyer": buyer}}).encode()
    signature = hashlib.md5(body + SECOND_KEY.encode()).hexdigest()  # noqa: S324 - PayU's documented algorithm
    request = RequestFactory().post(
        "/notify/payu/",
        data=body,
        content_type="application/json",
        HTTP_OPENPAYU_SIGNATURE=f"sender=checkout;signature={signature};algorithm=MD5;content=DOCUMENT",
    )

    response = payu_notify(request)

    assert response.status_code == 200  # verified on the raw body, buyer included
    intent.refresh_from_db()
    assert intent.payment_status == PaymentIntentStatus.CANCELLED
    assert intent.provider_notify == {"order": {"orderId": "PAYU-LATE-1", "status": "CANCELED"}}


# --- Backfill of payments stored before -------------------------------------------------------------------------

LEGACY_NOTIFY = {"order": {"orderId": "PAYU-OLD", "status": "COMPLETED", "buyer": {"email": EMAIL, "phone": PHONE}}}
LEGACY_REQUEST = {"extOrderId": "25-000009", "totalAmount": "100", "customerIp": IP, "description": f"{FIRST} {LAST}"}


def _legacy(channel, request=LEGACY_REQUEST, notify=LEGACY_NOTIFY) -> PaymentIntent:
    """A payment as a release before stored it: the payloads written whole (``update()`` skips ``save()``)."""
    intent = _intent(channel, PaymentProvider.PAYU, code=f"payu-{PaymentIntent.objects.count()}")
    PaymentIntent.objects.filter(pk=intent.pk).update(provider_request=request, provider_notify=notify)
    return intent


def _scrub_gateway_payloads(*args) -> str:
    out = StringIO()
    call_command("scrub-gateway-payloads", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_scrub_gateway_payloads_is_a_dry_run_by_default(channel):
    legacy = [_legacy(channel), _legacy(channel, notify=f"<itn><email>{EMAIL}</email></itn>")]
    clean = _intent(channel, PaymentProvider.STRIPE)
    clean.provider_notify = {"id": "cs_1", "payment_status": "paid"}
    clean.save()
    _intent(channel, PaymentProvider.COD)  # no payloads at all

    output = _scrub_gateway_payloads()

    assert "Payments with gateway payloads: 3; 2 hold data the allowlist drops" in output
    assert _leftovers(output) == []
    for intent in legacy:
        intent.refresh_from_db()
        assert intent.provider_request == LEGACY_REQUEST


@pytest.mark.django_db
def test_scrub_gateway_payloads_apply_reduces_them_in_batches_once(channel, monkeypatch):
    legacy = [_legacy(channel) for _ in range(5)]
    batches = []
    bulk_update = PaymentIntent.objects.bulk_update
    monkeypatch.setattr(
        PaymentIntent.objects,
        "bulk_update",
        lambda rows, fields: batches.append(len(rows)) or bulk_update(rows, fields),
    )

    output = _scrub_gateway_payloads("--apply", "--batch-size", "2")

    assert "Reduced the gateway payloads of 5 of 5 payments." in output
    assert batches == [2, 2, 1]
    for intent in legacy:
        intent.refresh_from_db()
        assert _leftovers(intent.provider_request, intent.provider_notify) == []
        assert intent.provider_request == {"extOrderId": "25-000009", "totalAmount": "100"}
        assert intent.provider_notify == {"order": {"orderId": "PAYU-OLD", "status": "COMPLETED"}}
    assert "5; 0 hold data the allowlist drops" in _scrub_gateway_payloads()  # idempotent
