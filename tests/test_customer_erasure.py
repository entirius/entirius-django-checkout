# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Customer erasure (GDPR) left personal data behind.

``Order.anonymize()`` rewrote the names and e-mails inside ``order_body`` and nothing else:

- the order's ``billing_email`` / ``shipping_email`` columns and the ``UsedCoupon`` rows kept the real address;
- the street, city, postcode, phone, company and tax id of both addresses, the customer's comment and IP stayed in
  the body (of the order and of its cart);
- every ``PaymentIntent`` kept the gateway payloads: ``provider_request`` (what checkout sent: name, e-mail, phone,
  address) and ``provider_notify`` (what the gateway answered: the payer, the shipping address).

A repeated erasure request could not repair it: orders were found by the customer account and by ``order_body``
only, never by the columns or by the e-mail a used code stored.

Every personal value below is invented.
"""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import RequestFactory
from django.utils import timezone
from process_logger import ProcessLogger
from rest_framework.test import APIRequestFactory

from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Cart, Order, PaymentIntent, PaymentRedirect, UsedCoupon
from django_checkout.services.customer import BlockingOrder, CustomerService, ErasureBlocked
from django_checkout.signals import customer_anonymized_signal
from django_checkout.views.admin.customer import admin_customer_delete
from django_checkout.views.payment_redirect import payment_redirect_bridge
from tests.conftest import ERASE_SCOPE

EMAIL = "anna.testowa@example.org"
ACCOUNT_EMAIL = "anna.konto@example.org"
OTHER_EMAIL = "piotr.inny@example.org"
FIRST, LAST = "Anna", "Testowa"
STREET, CITY, POSTCODE = "ul. Wymyslona 7", "Testowo", "00-950"
PHONE = "600100200"
COMPANY, TAX_ID = "Pracownia Anna Testowa", "0000000000"
IP = "192.0.2.10"
COMMENT = "Please call before the delivery"
BLIK_CODE = "777123"
PERSONAL = (EMAIL, ACCOUNT_EMAIL, FIRST, LAST, STREET, CITY, POSTCODE, PHONE, COMPANY, TAX_ID, IP, COMMENT, BLIK_CODE)

ANONYMIZED_EMAIL = "xxxxxxxxxxxx@example.com"  # anonymize() of a 12-character local part


def _address(email=EMAIL, **extra):
    return {
        "firstname": FIRST,
        "lastname": LAST,
        "email": email,
        "street": STREET,
        "city": CITY,
        "postcode": POSTCODE,
        "country_code": "PL",
        "dialling_code": "+48",
        "telephone": PHONE,
        "company": COMPANY,
        "address_id": None,
        "external_id": None,
        **extra,
    }


def _body(email=EMAIL):
    return {
        "addresses": {
            "billing_address": _address(email, tax_id=TAX_ID, requested_invoice=True),
            "shipping_address": _address(email),
        },
        "comment": COMMENT,
        "customer_ip": IP,
        "affiliate_code": "PARTNER-1",
        "total": "123.00",
        "currency_code": "PLN",
        "country_code": "PL",
    }


def _order(channel, email=EMAIL, **fields):
    fields.setdefault("order_status", OrderStatus.COMPLETED)  # delivered: nothing refuses its erasure
    return Order.objects.create(
        channel=channel, order_body=_body(email), billing_email=email, shipping_email=email, **fields
    )


def _dump(value) -> str:
    return json.dumps(value)


def _leftovers(value) -> list[str]:
    dumped = _dump(value)
    return [personal for personal in PERSONAL if personal in dumped]


# --- Gateway payloads -------------------------------------------------------------------------------------------
# Shapes follow what each provider stores today (the SDK DTOs and the notify bodies); values are invented.

PAYU = (
    {
        "customerIp": IP,
        "merchantPosId": "300746",
        "description": "Order 25-000001",
        "totalAmount": "12300",
        "buyer": {"email": EMAIL, "phone": PHONE, "firstName": FIRST, "lastName": LAST, "language": "pl"},
        "products": [{"name": "Mug", "unitPrice": "12300", "quantity": "1"}],
        "currencyCode": "PLN",
        "notifyUrl": "https://shop.example.com/notify/payu/",
        "extOrderId": "25-000001",
        "continueUrl": "https://shop.example.com/thanks",
        "payMethods": {"payMethod": {"type": "BLIK_AUTHORIZATION_CODE", "value": BLIK_CODE}},
    },
    {
        "order": {
            "orderId": "PAYU-ORDER-1",
            "extOrderId": "25-000001",
            "orderCreateDate": "2026-11-01T10:00:00.000+01:00",
            "customerIp": IP,
            "merchantPosId": "300746",
            "description": "Order 25-000001",
            "currencyCode": "PLN",
            "totalAmount": "12300",
            "buyer": {"email": EMAIL, "phone": PHONE, "firstName": FIRST, "lastName": LAST},
            "status": "COMPLETED",
        },
        "localReceiptDateTime": "2026-11-01T10:01:00.000+01:00",
    },
    [
        ("request", ("totalAmount",), "12300"),
        ("request", ("currencyCode",), "PLN"),
        ("request", ("extOrderId",), "25-000001"),
        ("notify", ("order", "orderId"), "PAYU-ORDER-1"),
        ("notify", ("order", "status"), "COMPLETED"),
        ("notify", ("order", "totalAmount"), "12300"),
    ],
)

PAYPAL = (
    {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": "25-000001",
                "amount": {"currency_code": "PLN", "value": "123.00"},
                "shipping": {
                    "name": {"full_name": f"{FIRST} {LAST}"},
                    "address": {"address_line_1": STREET, "admin_area_2": CITY, "postal_code": POSTCODE},
                },
            }
        ],
        "payer": {
            "email_address": EMAIL,
            "phone": {"phone_number": {"national_number": PHONE}},
            "name": {"given_name": FIRST, "surname": LAST},
        },
    },
    {
        "id": "PAYPAL-ORDER-1",
        "status": "COMPLETED",
        "payer": {"name": {"given_name": FIRST, "surname": LAST}, "email_address": EMAIL, "payer_id": "PAYER-ID-1"},
        "purchase_units": [
            {
                "reference_id": "25-000001",
                "shipping": {"name": {"full_name": f"{FIRST} {LAST}"}, "address": {"address_line_1": STREET}},
                "payments": {
                    "captures": [
                        {
                            "id": "CAPTURE-1",
                            "status": "COMPLETED",
                            "amount": {"currency_code": "PLN", "value": "123.00"},
                        }
                    ]
                },
            }
        ],
        "links": [{"href": "https://api.example.com/v2/checkout/orders/PAYPAL-ORDER-1", "rel": "self"}],
    },
    [
        ("request", ("purchase_units", 0, "amount", "value"), "123.00"),
        ("notify", ("id",), "PAYPAL-ORDER-1"),
        ("notify", ("status",), "COMPLETED"),
        ("notify", ("purchase_units", 0, "payments", "captures", 0, "id"), "CAPTURE-1"),
        ("notify", ("purchase_units", 0, "payments", "captures", 0, "amount", "value"), "123.00"),
    ],
)

STRIPE = (
    {
        "client_reference_id": "25-000001",
        "mode": "payment",
        "success_url": "https://shop.example.com/thanks",
        "line_items": [{"price_data": {"currency": "pln", "unit_amount": 12300}, "quantity": 1}],
        "customer_email": EMAIL,
    },
    {
        "id": "cs_test_1",
        "amount_total": 12300,
        "currency": "pln",
        "customer_email": EMAIL,
        "customer_details": {
            "email": EMAIL,
            "name": f"{FIRST} {LAST}",
            "phone": PHONE,
            "address": {"line1": STREET, "city": CITY, "postal_code": POSTCODE, "country": "PL"},
            "tax_ids": [{"type": "eu_vat", "value": TAX_ID}],
        },
        "payment_intent": "pi_test_1",
        "payment_status": "paid",
        "status": "complete",
    },
    [
        ("request", ("client_reference_id",), "25-000001"),
        ("notify", ("id",), "cs_test_1"),
        ("notify", ("amount_total",), 12300),
        ("notify", ("payment_intent",), "pi_test_1"),
        ("notify", ("payment_status",), "paid"),
    ],
)

PRZELEWY24 = (
    {
        "merchantId": 11111,
        "posId": 11111,
        "sessionId": "25-000001",
        "amount": 12300,
        "currency": "PLN",
        "description": "Order 25-000001",
        "email": EMAIL,
        "country": "PL",
        "client": f"{FIRST} {LAST}",
        "address": STREET,
        "zip": POSTCODE,
        "city": CITY,
        "phone": PHONE,
        "transferLabel": COMMENT,
        "sign": "synthetic-sign",
        "additional": {"shipping": {"type": 0, "address": STREET, "zip": POSTCODE, "city": CITY, "country": "PL"}},
    },
    {
        "merchantId": 11111,
        "posId": 11111,
        "sessionId": "25-000001",
        "amount": 12300,
        "originAmount": 12300,
        "currency": "PLN",
        "orderId": 300000001,
        "methodId": 154,
        "statement": "p24-A1-B2",
        "sign": "synthetic-sign",
    },
    [
        ("request", ("sessionId",), "25-000001"),
        ("request", ("amount",), 12300),
        ("notify", ("orderId",), 300000001),
        ("notify", ("amount",), 12300),
    ],
)

PAYNOW = (
    {
        "amount": 12300,
        "currency": "PLN",
        "description": "Order 25-000001",
        "externalId": "25-000001",
        "buyer": {
            "email": EMAIL,
            "firstName": FIRST,
            "lastName": LAST,
            "phone": {"prefix": "+48", "number": int(PHONE)},
            "address": {"billing": {"street": STREET, "zipcode": POSTCODE, "city": CITY, "country": "PL"}},
        },
    },
    {"paymentId": "PAYNOW-1", "externalId": "25-000001", "status": "CONFIRMED", "modifiedAt": "2026-11-01T10:01:00"},
    [
        ("request", ("externalId",), "25-000001"),
        ("request", ("amount",), 12300),
        ("notify", ("paymentId",), "PAYNOW-1"),
        ("notify", ("status",), "CONFIRMED"),
    ],
)

AUTOPAY = (
    {
        "ServiceID": "100001",
        "OrderID": "25-000001",
        "Amount": "123.00",
        "Description": f"{FIRST} {LAST}",
        "GatewayID": 0,
        "Currency": "PLN",
        "CustomerEmail": EMAIL,
        "Title": "Shop",
        "Hash": "synthetic-hash",
    },
    {"order_id": "25-000001", "redirect_url": "https://pay.example.com/continue?token=synthetic", "status": "OK"},
    [
        ("request", ("OrderID",), "25-000001"),
        ("request", ("Amount",), "123.00"),
        ("notify", ("order_id",), "25-000001"),
    ],
)

GATEWAYS = {
    "payu": PAYU,
    "paypal": PAYPAL,
    "stripe": STRIPE,
    "przelewy24": PRZELEWY24,
    "paynow": PAYNOW,
    "autopay": AUTOPAY,
}


def _at(payload, path):
    for step in path:
        payload = payload[step]
    return payload


def _intent(order, code):
    """A payment whose payloads were stored whole, as before every save went through the allowlist."""
    request, notify, _ = GATEWAYS[code]
    intent = PaymentIntent.objects.create(
        order=order,
        code=code,
        amount="123.00",
        currency="PLN",
        external_order_id=f"{code.upper()}-EXT-1",
        payment_status=PaymentIntentStatus.COMPLETE,
    )
    PaymentIntent.objects.filter(pk=intent.pk).update(provider_request=request, provider_notify=notify)  # no save()
    intent.refresh_from_db()
    return intent


# --- Order.anonymize --------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymize_clears_the_email_columns(channel):
    order = _order(channel)

    assert order.anonymize() is True

    order.refresh_from_db()
    assert (order.billing_email, order.shipping_email) == (ANONYMIZED_EMAIL, ANONYMIZED_EMAIL)


@pytest.mark.django_db
def test_anonymize_clears_the_columns_even_without_addresses_in_the_body(channel):
    order = Order.objects.create(channel=channel, order_body={}, billing_email=EMAIL)

    assert order.anonymize() is True

    order.refresh_from_db()
    assert order.billing_email == ANONYMIZED_EMAIL
    assert order.shipping_email is None


@pytest.mark.django_db
def test_anonymize_clears_the_emails_of_the_used_codes(channel):
    """A logged-in customer's code usage stores the account e-mail, not the address one."""
    order = _order(channel)
    UsedCoupon.objects.create(
        channel=channel, order=order, billing_email=ACCOUNT_EMAIL, shipping_email=EMAIL, discount_code="WELCOME10"
    )

    order.anonymize()

    coupon = order.used_coupons.get()
    assert _leftovers([coupon.billing_email, coupon.shipping_email]) == []
    assert coupon.billing_email.endswith("@example.com")
    assert coupon.discount_code == "WELCOME10"


@pytest.mark.django_db
def test_anonymize_clears_every_contact_field_of_both_addresses(channel):
    order = _order(channel)

    order.anonymize()

    order.refresh_from_db()
    addresses = order.order_body["addresses"]
    assert _leftovers(addresses) == []
    for address in addresses.values():
        assert address["street"] == "x" * len(STREET)
        assert address["telephone"] == "x" * len(PHONE)
        assert (address["country_code"], address["dialling_code"]) == ("PL", "+48")
    assert addresses["billing_address"]["tax_id"] == "x" * len(TAX_ID)
    assert addresses["billing_address"]["requested_invoice"] is True


@pytest.mark.django_db
def test_anonymize_clears_the_comment_and_the_customer_ip(channel):
    order = _order(channel)

    order.anonymize()

    order.refresh_from_db()
    assert _leftovers(order.order_body) == []
    assert order.order_body["affiliate_code"] == "PARTNER-1"
    assert order.order_body["total"] == "123.00"


@pytest.mark.django_db
@pytest.mark.parametrize("code", GATEWAYS)
def test_anonymize_drops_personal_data_from_the_gateway_payloads(channel, code):
    order = _order(channel)
    intent = _intent(order, code)

    order.anonymize()

    intent.refresh_from_db()
    assert _leftovers(intent.provider_request) == []
    assert _leftovers(intent.provider_notify) == []


@pytest.mark.django_db
@pytest.mark.parametrize("code", GATEWAYS)
def test_anonymize_keeps_what_accounting_needs_from_the_gateway_payloads(channel, code):
    order = _order(channel)
    intent = _intent(order, code)

    order.anonymize()

    intent.refresh_from_db()
    payloads = {"request": intent.provider_request, "notify": intent.provider_notify}
    for which, path, value in GATEWAYS[code][2]:
        assert _at(payloads[which], path) == value, (which, path)
    assert (str(intent.amount), intent.currency) == ("123.00", "PLN")
    assert intent.external_order_id == f"{code.upper()}-EXT-1"
    assert intent.payment_status == PaymentIntentStatus.COMPLETE


@pytest.mark.django_db
def test_anonymize_drops_the_paypal_payer_id(channel):
    """A gateway account id identifies the person as well as the e-mail does."""
    order = _order(channel)
    intent = _intent(order, "paypal")

    order.anonymize()

    intent.refresh_from_db()
    assert "PAYER-ID-1" not in _dump(intent.provider_notify)


@pytest.mark.django_db
def test_anonymize_is_idempotent(channel):
    order = _order(channel)
    intent = _intent(order, "paypal")
    order.anonymize()
    order.refresh_from_db()
    intent.refresh_from_db()
    first = (order.order_body, order.billing_email, intent.provider_request, intent.provider_notify)

    order.anonymize()

    order.refresh_from_db()
    intent.refresh_from_db()
    assert (order.order_body, order.billing_email, intent.provider_request, intent.provider_notify) == first


@pytest.mark.django_db
def test_anonymize_redacts_the_payment_redirect_target(channel):
    """The bridge's target is a copy of the body's ``continue_url``, which the body scrub already redacts."""
    order = _order(channel)
    target = f"exp://shop.example/thanks?email={EMAIL}"
    redirect = PaymentRedirect.objects.create(order=order, target_url=target, expires_at=timezone.now())

    assert order.anonymize() is True

    redirect.refresh_from_db()
    assert redirect.target_url == "***REDACTED***"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("code", "link"),
    [
        ("payu_blik", f"https://shop.example.com/thanks/25-000001?email={EMAIL}"),  # BLIK: the continue_url copy
        ("payu", "https://secure.snd.payu.com/pay/?orderId=PAYU-EXT-1&token=synthetic"),  # the gateway's page
    ],
    ids=["blik-continue-url", "gateway-page"],
)
def test_anonymize_redacts_the_payment_link_and_keeps_the_accounting_columns(channel, code, link):
    """Only the order's creation answer hands the link out; accounting reads external_order_id, not the link."""
    order = _order(channel)
    intent = PaymentIntent.objects.create(
        order=order, code=code, amount="123.00", currency="PLN", external_order_id="EXT-1", redirect_url=link
    )

    order.anonymize()

    intent.refresh_from_db()
    assert intent.redirect_url == "***REDACTED***"
    assert (str(intent.amount), intent.currency, intent.external_order_id) == ("123.00", "PLN", "EXT-1")


@pytest.mark.django_db
def test_a_redacted_payment_redirect_answers_404(channel, monkeypatch):
    """A bridge link still open after the erasure leads nowhere instead of to the old target."""
    from django_checkout import settings as checkout_settings

    monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES", ["exp"])
    order = _order(channel)
    expires_at = timezone.now() + timezone.timedelta(minutes=30)
    redirect = PaymentRedirect.objects.create(
        order=order, target_url="exp://shop.example/thanks", expires_at=expires_at
    )
    order.anonymize()

    response = payment_redirect_bridge(RequestFactory().get("/"), "1", channel.idx, redirect.token)

    assert response.status_code == 404


@pytest.mark.django_db
def test_cart_anonymize_clears_every_contact_field(channel):
    cart = Cart.objects.create(channel=channel, cart_body=_body())

    assert cart.anonymize() is True

    cart.refresh_from_db()
    assert _leftovers(cart.cart_body) == []
    assert cart.cart_body["addresses"]["shipping_address"]["country_code"] == "PL"


def test_gateway_payload_of_an_unknown_shape_is_dropped():
    from django_checkout.utils.gateway_payload import scrub_gateway_payload

    assert scrub_gateway_payload(None) is None
    assert scrub_gateway_payload(f"<transaction><email>{EMAIL}</email></transaction>") is None
    assert scrub_gateway_payload([{"status": "PAID", "email": EMAIL}]) == [{"status": "PAID"}]


# --- The erasure request ----------------------------------------------------------------------------------------


def _erase(email, channel=None):
    service = CustomerService()
    service.set_logger(ProcessLogger("TEST", module="django_checkout"))
    return service.anonymize_customer(email, channel)


def _old_marker_body(marker="xxxxxxxxxxxx@example.com"):
    """order_body as the old anonymize() left it: names and e-mails replaced, the rest kept."""
    body = _body(marker)
    for address in body["addresses"].values():
        address["firstname"], address["lastname"] = "x" * len(FIRST), "x" * len(LAST)
    return body


@pytest.fixture
def half_anonymized_order(channel):
    """State the old anonymize() left behind: names and e-mails gone from the body, everything else kept."""
    order = Order.objects.create(
        channel=channel,
        order_body=_old_marker_body(),
        billing_email=EMAIL,
        shipping_email=EMAIL,
        order_status=OrderStatus.COMPLETED,
    )
    UsedCoupon.objects.create(
        channel=channel, order=order, billing_email=EMAIL, shipping_email=EMAIL, discount_code="WELCOME10"
    )
    _intent(order, "payu")
    return order


@pytest.mark.django_db
def test_erasure_request_finds_the_order_by_its_email_columns(half_anonymized_order):
    success, orders_idxs, *_ = _erase(EMAIL.upper())

    assert success is True
    assert orders_idxs == [str(half_anonymized_order.order_id)]
    half_anonymized_order.refresh_from_db()
    assert half_anonymized_order.billing_email == ANONYMIZED_EMAIL
    assert _leftovers(half_anonymized_order.order_body) == []


@pytest.mark.django_db
def test_erasure_request_finds_the_order_by_the_email_of_a_used_code(channel):
    """The account is gone (customer set to NULL); only the used code still holds the account e-mail."""
    order = _order(channel)
    UsedCoupon.objects.create(
        channel=channel, order=order, billing_email=ACCOUNT_EMAIL, shipping_email="", discount_code="WELCOME10"
    )
    intent = _intent(order, "stripe")

    success, orders_idxs, *_ = _erase(ACCOUNT_EMAIL)

    assert success is True
    assert orders_idxs == [str(order.order_id)]
    order.refresh_from_db()
    intent.refresh_from_db()
    assert _leftovers([order.order_body, order.billing_email, order.used_coupons.get().billing_email]) == []
    assert _leftovers([intent.provider_request, intent.provider_notify]) == []


@pytest.mark.django_db
def test_erasure_request_leaves_other_customers_alone(channel):
    erased = _order(channel)
    other = _order(channel, email=OTHER_EMAIL)
    other_intent = _intent(other, "payu")

    _erase(EMAIL)

    other.refresh_from_db()
    other_intent.refresh_from_db()
    assert other.billing_email == OTHER_EMAIL
    assert other.order_body == _body(OTHER_EMAIL)
    assert other_intent.provider_request == PAYU[0]
    erased.refresh_from_db()
    assert erased.billing_email == ANONYMIZED_EMAIL


@pytest.mark.django_db
def test_erasure_request_leaves_an_email_that_merely_contains_the_erased_one(channel):
    """``jo`` + the erased address is another customer: the body matches the whole JSON string, not a substring."""
    longer = f"jo{EMAIL}"
    other_order = _order(channel, email=longer)
    other_order.billing_email = other_order.shipping_email = None  # found by its body only
    other_order.save()
    other_cart = Cart.objects.create(channel=channel, cart_body=_body(longer))

    success, orders_idxs, carts_idxs, *_ = _erase(EMAIL)

    assert (success, orders_idxs, carts_idxs) == (True, [], [])
    other_order.refresh_from_db()
    other_cart.refresh_from_db()
    assert other_order.order_body == _body(longer)
    assert other_cart.cart_body == _body(longer)


def _gift_for(recipient_email: str, owner_email: str = OTHER_EMAIL) -> dict:
    """A body of ``owner_email`` with a gift card for ``recipient_email`` and a comment naming them."""
    body = _body(owner_email)
    gift = {"recipient_email": recipient_email, "recipient_name": "Basia", "message": "Happy birthday"}
    body["cart"] = {"items": [{"sku": "GIFT-CARD-50", "quantity": 1, "voucher_gift": gift}]}
    body["comment"] = f"Please send the card to {recipient_email.upper()} by Friday"
    return body


@pytest.mark.django_db
def test_another_customers_order_keeps_everything_but_the_erased_email(channel):
    """B is only the recipient of A's gift card: A's names, addresses and columns stay, B's e-mail goes."""
    order = Order.objects.create(
        channel=channel,
        order_body=_gift_for(EMAIL),
        billing_email=OTHER_EMAIL,
        shipping_email=OTHER_EMAIL,
        order_status=OrderStatus.COMPLETED,
    )
    cart = Cart.objects.create(channel=channel, cart_body=_gift_for(EMAIL))

    success, orders_idxs, carts_idxs, *_ = _erase(EMAIL)

    assert (success, orders_idxs, carts_idxs) == (True, [str(order.order_id)], [str(cart.cart_id)])
    for row, body in ((order, "order_body"), (cart, "cart_body")):
        row.refresh_from_db()
        stored = getattr(row, body)
        assert EMAIL not in json.dumps(stored).lower()
        assert stored["addresses"] == _body(OTHER_EMAIL)["addresses"]
        gift = stored["cart"]["items"][0]["voucher_gift"]
        assert gift == {"recipient_email": ANONYMIZED_EMAIL, "recipient_name": "Basia", "message": "Happy birthday"}
        assert stored["comment"] == f"Please send the card to {ANONYMIZED_EMAIL} by Friday"
    assert (order.billing_email, order.shipping_email) == (OTHER_EMAIL, OTHER_EMAIL)


@pytest.mark.django_db
def test_another_customers_open_order_holding_the_email_refuses_the_erasure(channel):
    """The shop may still have to deliver A's gift card to B's address."""
    order = Order.objects.create(
        channel=channel, order_body=_gift_for(EMAIL), billing_email=OTHER_EMAIL, order_status=OrderStatus.CONFIRMED
    )

    with pytest.raises(ErasureBlocked) as refused:
        _erase(EMAIL)

    assert [blocking.order_id for blocking in refused.value.orders] == [str(order.order_id)]
    order.refresh_from_db()
    assert order.order_body == _gift_for(EMAIL)


# --- Orders the shop still has to fulfil refuse the erasure ------------------------------------------------------

OPEN_STATUSES = [
    OrderStatus.NEW,
    OrderStatus.UNPAID,
    OrderStatus.CONFIRMED,
    OrderStatus.HOLDED,
    OrderStatus.IN_PROGRESS,
]
SETTLED_STATUSES = [OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED]


def _untouched(*rows) -> bool:
    for row in rows:
        row.refresh_from_db()
    return all((getattr(row, "order_body", None) or row.cart_body) == _body() for row in rows)


@pytest.fixture
def anonymized_signals():
    received = []

    def receiver(sender, **kwargs):
        received.append(kwargs)

    customer_anonymized_signal.connect(receiver)
    yield received
    customer_anonymized_signal.disconnect(receiver)


@pytest.mark.django_db
@pytest.mark.parametrize("status", OPEN_STATUSES)
def test_erasure_is_refused_while_an_order_is_not_fulfilled(channel, status):
    order = _order(channel, order_status=status)
    intent = _intent(order, "payu")
    cart = Cart.objects.create(channel=channel, cart_body=_body())

    with pytest.raises(ErasureBlocked) as refused:
        _erase(EMAIL)

    assert refused.value.orders == [BlockingOrder(str(order.order_id), order.pretty_id, status)]
    assert _untouched(order, cart)
    assert order.billing_email == EMAIL
    intent.refresh_from_db()
    assert intent.provider_request == PAYU[0]


@pytest.mark.django_db
@pytest.mark.parametrize("status", SETTLED_STATUSES)
def test_erasure_goes_through_once_every_order_is_settled(channel, status):
    order = _order(channel, order_status=status)

    success, orders_idxs, *_ = _erase(EMAIL)

    assert (success, orders_idxs) == (True, [str(order.order_id)])
    order.refresh_from_db()
    assert order.billing_email == ANONYMIZED_EMAIL


@pytest.mark.django_db
def test_one_open_order_keeps_every_order_and_cart_of_the_customer(channel, anonymized_signals):
    delivered = _order(channel)
    in_progress = _order(channel, order_status=OrderStatus.IN_PROGRESS)
    cart = Cart.objects.create(channel=channel, cart_body=_body())

    with pytest.raises(ErasureBlocked) as refused:
        _erase(EMAIL)

    assert [order.order_id for order in refused.value.orders] == [str(in_progress.order_id)]
    assert _untouched(delivered, in_progress, cart)
    assert anonymized_signals == []


@pytest.mark.django_db
def test_a_channel_erasure_looks_only_at_the_orders_of_its_channel(channel, other_channel):
    own = _order(channel)
    elsewhere = _order(other_channel, order_status=OrderStatus.CONFIRMED)

    success, orders_idxs, *_ = _erase(EMAIL, channel)

    assert (success, orders_idxs) == (True, [str(own.order_id)])
    assert _untouched(elsewhere)
    with pytest.raises(ErasureBlocked):
        _erase(EMAIL)  # every channel: the confirmed order refuses it


@pytest.mark.django_db
def test_erase_view_answers_409_naming_the_open_orders_and_nothing_personal(channel, make_api_key):
    order = _order(channel, order_status=OrderStatus.CONFIRMED)
    key = make_api_key(channel=channel, scope=ERASE_SCOPE)
    request = APIRequestFactory().delete(
        "/", data=json.dumps({"email": EMAIL}), content_type="application/json", HTTP_X_API_ADMIN_KEY=key
    )

    response = admin_customer_delete(request, channel_idx=channel.idx, version="1")

    assert response.status_code == 409
    body = json.loads(response.content)
    assert body["meta"]["status"] == "OPEN_ORDERS"
    assert "Cancel or finish the listed orders, then erase again" in body["meta"]["message"]
    assert body["data"] == {
        "deleted": False,
        "blocking_orders": [{"order_id": str(order.order_id), "pretty_id": order.pretty_id, "status": "confirmed"}],
    }
    assert _leftovers(response.content.decode()) == []
    assert _untouched(order)


@pytest.mark.django_db
def test_python_api_raises_erasure_blocked(channel):
    from django_checkout.output.admin import ErasureBlocked as PublicErasureBlocked
    from django_checkout.output.admin import admin_customer_delete as erase_customer

    order = _order(channel, order_status=OrderStatus.UNPAID)

    with pytest.raises(PublicErasureBlocked):
        erase_customer(EMAIL, ProcessLogger("TEST", module="django_checkout"))

    assert _untouched(order)


# --- Backfill of orders erased before the fix -------------------------------------------------------------------


def _complete_erasures(*args) -> str:
    out = StringIO()
    call_command("complete-erasures", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_complete_erasures_is_a_dry_run_by_default(half_anonymized_order):
    output = _complete_erasures()

    assert "orders 1, carts 0" in output
    assert "1 still hold an e-mail" in output
    assert EMAIL not in output
    half_anonymized_order.refresh_from_db()
    assert half_anonymized_order.billing_email == EMAIL
    assert half_anonymized_order.payment_items.get().provider_request == PAYU[0]


@pytest.mark.django_db
def test_complete_erasures_apply_finishes_the_old_erasures_only(channel, half_anonymized_order):
    old_line_marker = Order.objects.create(
        channel=channel, order_body=_old_marker_body("xxxxxxxxxxxx@old-line.example"), billing_email=EMAIL
    )
    erased_cart = Cart.objects.create(channel=channel, cart_body=_old_marker_body())
    untouched = _order(channel, email=OTHER_EMAIL)

    assert "orders 1, carts 1" in _complete_erasures()  # the old line's marker only on request

    output = _complete_erasures("--apply", "--marker-domain", "old-line.example")

    assert "orders 2, carts 1, failed 0" in output
    for order in (half_anonymized_order, old_line_marker):
        order.refresh_from_db()
        assert _leftovers([order.order_body, order.billing_email, order.shipping_email]) == []
    intent = half_anonymized_order.payment_items.get()
    assert _leftovers([intent.provider_request, intent.provider_notify]) == []
    assert _leftovers([half_anonymized_order.used_coupons.get().billing_email]) == []
    erased_cart.refresh_from_db()
    assert _leftovers(erased_cart.cart_body) == []
    untouched.refresh_from_db()
    assert untouched.order_body == _body(OTHER_EMAIL)


@pytest.mark.django_db
def test_complete_erasures_is_not_refused_by_an_open_order(channel):
    """The backfill finishes erasures already made; it never starts one, so open orders do not stop it."""
    order = Order.objects.create(
        channel=channel, order_body=_old_marker_body(), billing_email=EMAIL, order_status=OrderStatus.IN_PROGRESS
    )

    assert "orders 1, carts 0, failed 0" in _complete_erasures("--apply")

    order.refresh_from_db()
    assert order.billing_email == ANONYMIZED_EMAIL
