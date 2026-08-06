# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v1 customer order endpoints — payment-secret redaction and per-page query cost.

``GET /api/checkout/1/{channel}/orders/`` and ``orders/{uid}/`` returned ``order_body``
verbatim, so ``card``, ``authorization_token``, ``continue_url`` and ``pay_code`` left the
API in the clear. v2 has run everything through ``redact_payment_secrets`` since 8.2.0;
v1 did not, and the Nuxt storefront is on v1 — so this was the production path. Nothing
covered these two endpoints.

The same view also resolved method names, status labels, attachments, invoices and
shipping intents once per order, so the cost of a page grew with the number of orders on it.

The v1 views are plain Django function views and this package has no ROOT_URLCONF, so
``order_view`` is called directly. Channel auth comes from the ``X-API-KEY`` header,
customer auth from a real JWT through ``django_accounts.backends.JWTAccessBackend``.
"""

import json

import pytest
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rest_framework_simplejwt.tokens import RefreshToken

from django_checkout.enums import OrderStatus
from django_checkout.models import (
    Invoice,
    Order,
    OrderAttachment,
    OrderStatusLabel,
    PaymentMethod,
    ShippingIntent,
    ShippingMethod,
)
from django_checkout.utils import REDACTED
from django_checkout.views.order import get_order_attachment, order_view

factory = RequestFactory()

CARD_TOKEN = "tok_1QsyntheticCardToken"  # noqa: S105 — synthetic fixture, not a real credential
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiJ9.synthetic-authorization-token"  # noqa: S105 — synthetic fixture
CONTINUE_URL = "https://pay.example.com/continue/<order_id>?sig=synthetic-signature"
BLIK_CODE = "123456"
VOUCHER_CODE = "26WT****"


def _payment(code, **overrides):
    entry = {"code": code, "name": "Stored name", "card": None, "authorization_token": None, "continue_url": None}
    entry.update(overrides)
    return entry


def _body(payment_method):
    """Minimal order_body — only the keys the v1 representation reads."""
    return {
        "total": "419.97",
        "currency_code": "EUR",
        "country_code": "PL",
        "cart": {"items": [{"sku": "ENT-C002", "quantity": 1}]},
        "shipping_method": {"code": "courier", "name": "Stored shipping name"},
        "payment_method": payment_method,
    }


@pytest.fixture
def customer_token(customer):
    return str(RefreshToken.for_user(customer.user).access_token)


@pytest.fixture
def methods(channel):
    """Returns the ShippingMethod — ShippingIntent needs it as an FK."""
    shipping = ShippingMethod.objects.create(
        channel=channel, code="courier", name_t9n={"en": "Courier", "pl": "Kurier"}
    )
    PaymentMethod.objects.create(channel=channel, code="card", name_t9n={"en": "Card", "pl": "Karta"})
    PaymentMethod.objects.create(channel=channel, code="voucher", name_t9n={"en": "Voucher", "pl": "Bon"})
    OrderStatusLabel.objects.create(channel=channel, status=OrderStatus.UNPAID, name_t9n={"en": "Awaiting payment"})
    return shipping


def _attach_relations(order, shipping_method):
    """Give an order one of everything order_to_repr dereferences.

    Without these rows the query-count test cannot see a dropped nested select_related:
    get_download_url (order -> customer) and shipping_intent.method are never reached.
    bulk_create skips OrderAttachment.save(), which would demand a real file.
    """
    OrderAttachment.objects.bulk_create([OrderAttachment(order=order, name="invoice.pdf")])
    ShippingIntent.objects.create(order=order, method=shipping_method, code="courier")
    Invoice.objects.create(order=order, invoice_number="FV/TEST/1", invoice_base64="")


def _order(channel, customer, payment_method, status=OrderStatus.UNPAID):
    return Order.objects.create(
        channel=channel, customer=customer, order_status=status, order_body=_body(payment_method)
    )


def _response(channel, api_key, token, uid=None, **params):
    headers = {"HTTP_X_API_KEY": api_key.key}
    if token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    request = factory.get("/orders/", params, **headers)
    kwargs = {"uid": uid} if uid is not None else {}
    return order_view(request, channel_idx=channel.idx, **kwargs)


def _call(channel, api_key, token, uid=None, **params):
    return json.loads(_response(channel, api_key, token, uid=uid, **params).content)


def _secret_entry():
    return _payment(
        "card", card=CARD_TOKEN, authorization_token=AUTH_TOKEN, continue_url=CONTINUE_URL, pay_code=BLIK_CODE
    )


# --- Redaction -------------------------------------------------------------------------


@pytest.mark.django_db
def test_list_redacts_payment_secrets(channel, api_key, customer, customer_token, methods):
    _order(channel, customer, [_secret_entry()])

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"][0]

    assert entry["card"] == REDACTED
    assert entry["authorization_token"] == REDACTED
    assert entry["continue_url"] == REDACTED
    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_detail_redacts_payment_secrets(channel, api_key, customer, customer_token, methods):
    order = _order(channel, customer, [_secret_entry()])

    entry = _call(channel, api_key, customer_token, uid=order.pretty_id_snap)["data"]["payment_method"][0]

    assert entry["card"] == REDACTED
    assert entry["authorization_token"] == REDACTED
    assert entry["continue_url"] == REDACTED
    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_no_secret_survives_anywhere_in_the_response(channel, api_key, customer, customer_token, methods):
    """Belt and braces: the raw values must not appear in the serialized body at all."""
    _order(channel, customer, [_secret_entry()])

    raw = json.dumps(_call(channel, api_key, customer_token))

    for secret in (CARD_TOKEN, AUTH_TOKEN, "synthetic-signature", BLIK_CODE):
        assert secret not in raw


@pytest.mark.django_db
def test_masked_voucher_code_stays_visible(channel, api_key, customer, customer_token, methods):
    """Support reads the masked tail to tell which voucher paid — it is not a credential."""
    _order(channel, customer, [_payment("voucher", pay_code=VOUCHER_CODE)])

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"][0]

    assert entry["pay_code"] == VOUCHER_CODE


@pytest.mark.django_db
def test_star_in_pay_code_does_not_buy_an_exemption(channel, api_key, customer, customer_token, methods):
    """Redaction is decided by provider, not by content — otherwise any caller keeps an
    arbitrary pay_code out of redaction just by prefixing it with a star."""
    _order(channel, customer, [_payment("card", pay_code="*not-a-voucher-at-all")])

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"][0]

    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_legacy_single_dict_payment_method_is_redacted(channel, api_key, customer, customer_token, methods):
    """Orders written before the multi-method refactor hold a dict, not a list."""
    _order(channel, customer, _secret_entry())

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"]

    assert entry["card"] == REDACTED
    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_null_secrets_are_left_alone(channel, api_key, customer, customer_token, methods):
    """Masking a null would claim a secret exists where none does."""
    _order(channel, customer, [_payment("card")])

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"][0]

    assert entry["card"] is None
    assert entry["authorization_token"] is None


@pytest.mark.django_db
def test_response_does_not_mutate_stored_order_body(channel, api_key, customer, customer_token, methods):
    """The view used to edit order.order_body in place; redaction must not reach the DB."""
    order = _order(channel, customer, [_secret_entry()])

    _call(channel, api_key, customer_token)

    order.refresh_from_db()
    assert order.order_body["payment_method"][0]["card"] == CARD_TOKEN
    assert "status_label" not in order.order_body


# --- Erasure (GDPR) --------------------------------------------------------------------
# Redaction hides secrets from the API; erasure has to remove them from the row.


@pytest.mark.django_db
def test_anonymize_scrubs_payment_secrets_from_the_order(channel, customer):
    """A gateway token used to outlive the erasure request that removed the customer."""
    order = _order(channel, customer, [_secret_entry()])

    assert order.anonymize() is True

    order.refresh_from_db()
    entry = order.order_body["payment_method"][0]
    assert entry["card"] == REDACTED
    assert entry["authorization_token"] == REDACTED
    assert entry["continue_url"] == REDACTED
    assert entry["pay_code"] == REDACTED


@pytest.mark.django_db
def test_anonymize_keeps_the_masked_voucher_code(channel, customer):
    """Same rule as the API: a masked code is not a credential."""
    order = _order(channel, customer, [_payment("voucher", pay_code=VOUCHER_CODE)])

    order.anonymize()

    order.refresh_from_db()
    assert order.order_body["payment_method"][0]["pay_code"] == VOUCHER_CODE


@pytest.mark.django_db
def test_anonymize_still_scrubs_addresses(channel, customer):
    """The pre-existing address scrub must not be lost to the new one."""
    order = _order(channel, customer, [_secret_entry()])
    order.order_body["addresses"] = {"billing_address": {"firstname": "Jan", "lastname": "Testowy"}}
    order.save()

    order.anonymize()

    order.refresh_from_db()
    billing = order.order_body["addresses"]["billing_address"]
    assert billing["firstname"] == "xxx"
    assert billing["lastname"] == "xxxxxxx"


# --- Access control --------------------------------------------------------------------
# Redacting the body is worth little if the wrong person can read the order at all.


@pytest.fixture
def other_customer(db):
    """A second customer, to prove orders are not readable across accounts."""
    from django.contrib.auth import get_user_model
    from django_accounts.models import Customer

    user = get_user_model().objects.create_user(
        username="other",
        password="other123",  # noqa: S106 — synthetic test account
        email="other@example.com",
    )
    return Customer.objects.create(user=user)


@pytest.mark.django_db
def test_detail_rejects_another_customers_order(channel, api_key, customer, other_customer, methods):
    order = _order(channel, customer, [_secret_entry()])
    intruder_token = str(RefreshToken.for_user(other_customer.user).access_token)

    response = _response(channel, api_key, intruder_token, uid=order.pretty_id_snap)

    assert response.status_code == 403
    assert CARD_TOKEN not in response.content.decode()


@pytest.mark.django_db
def test_unauthenticated_request_is_rejected(channel, api_key, customer, methods):
    _order(channel, customer, [_secret_entry()])

    response = _response(channel, api_key, token=None)

    assert response.status_code == 401
    assert CARD_TOKEN not in response.content.decode()


@pytest.mark.django_db
def test_unknown_order_is_not_found(channel, api_key, customer, customer_token, methods):
    assert _response(channel, api_key, customer_token, uid="0000000000").status_code == 404


# --- Attachment download ----------------------------------------------------------------
# The uid in the path is a secret in a URL, not authorization: URLs leak via Referer,
# proxy logs and history. Ownership is decided against the authenticated caller.


def _attachment(channel, api_key, token, order, attachment_pk, uid):
    headers = {"HTTP_X_API_KEY": api_key.key}
    if token is not None:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    request = factory.get("/file/", **headers)
    return get_order_attachment(
        request, channel_idx=channel.idx, order_id=str(order.order_id), file_id=attachment_pk, uid=str(uid)
    )


@pytest.mark.django_db
def test_attachment_rejects_unauthenticated_caller_holding_the_link(channel, api_key, customer, methods):
    """Knowing the URL used to be enough."""
    order = _order(channel, customer, [_payment("card")])
    attachment = OrderAttachment.objects.bulk_create([OrderAttachment(order=order, name="invoice.pdf")])[0]

    response = _attachment(channel, api_key, None, order, attachment.pk, customer.uid)

    assert response.status_code == 401


@pytest.mark.django_db
def test_attachment_rejects_another_customer_even_with_the_right_uid(
    channel, api_key, customer, other_customer, methods
):
    """The uid in the path names the owner — it must not authorize the caller."""
    order = _order(channel, customer, [_payment("card")])
    attachment = OrderAttachment.objects.bulk_create([OrderAttachment(order=order, name="invoice.pdf")])[0]
    intruder_token = str(RefreshToken.for_user(other_customer.user).access_token)

    response = _attachment(channel, api_key, intruder_token, order, attachment.pk, customer.uid)

    assert response.status_code == 403


# --- Localization ----------------------------------------------------------------------


@pytest.mark.django_db
def test_method_names_are_localized(channel, api_key, customer, customer_token, methods):
    _order(channel, customer, [_payment("card")])

    order_data = _call(channel, api_key, customer_token, lang="pl")["data"][0]

    assert order_data["shipping_method"]["name"] == "Kurier"
    assert order_data["payment_method"][0]["name"] == "Karta"


@pytest.mark.django_db
def test_unknown_method_code_keeps_stored_name(channel, api_key, customer, customer_token, methods):
    _order(channel, customer, [_payment("gone-from-channel")])

    entry = _call(channel, api_key, customer_token)["data"][0]["payment_method"][0]

    assert entry["name"] == "Stored name"


@pytest.mark.django_db
def test_status_label_is_localized(channel, api_key, customer, customer_token, methods):
    _order(channel, customer, [_payment("card")])

    assert _call(channel, api_key, customer_token)["data"][0]["status_label"] == "Awaiting payment"


# --- Query cost ------------------------------------------------------------------------


@pytest.mark.django_db
def test_list_query_count_does_not_grow_with_the_page(channel, api_key, customer, customer_token, methods):
    """The DoD: cost per page must be flat, not linear in the number of orders.

    Every order carries an attachment, an invoice and a shipping intent, so a dropped
    select_related inside the prefetches shows up here too — not just a dropped prefetch.
    """
    for _ in range(3):
        _attach_relations(_order(channel, customer, [_secret_entry()]), methods)
    with CaptureQueriesContext(connection) as small:
        assert len(_call(channel, api_key, customer_token, limit=100)["data"]) == 3

    for _ in range(9):
        _attach_relations(_order(channel, customer, [_secret_entry()]), methods)
    with CaptureQueriesContext(connection) as large:
        assert len(_call(channel, api_key, customer_token, limit=100)["data"]) == 12

    assert len(large) == len(small), (
        f"query count grew from {len(small)} to {len(large)} when the page went from 3 to 12 orders"
    )
