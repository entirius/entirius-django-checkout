# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 ``PATCH carts/{id}/payment/`` takes the return URL of the payment.

``cart_service.patch_payment`` wrote ``continue_url=None``, and the request schema had no such field: a v2 cart sent
PayU no ``continueUrl`` and Stripe no ``success_url``, which Stripe refuses a hosted session without. The URL is
stored with the payment method as v1 stores it, is never returned, and must be a target the payment step accepts
(``resolve_continue_url``): a direct scheme, or a target the payment bridge may redirect to.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIRequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.api.v2.views.cart_views import CartAddressesView, CartPaymentView
from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import PaymentProvider
from django_checkout.models import PaymentMethod
from django_checkout.services import cart_service

factory = APIRequestFactory()
RETURN_URL = "https://shop.example.org/checkout/success?ref=<order_id>"
BUYER = {
    "email": "buyer@example.org",
    "firstname": "Jan",
    "lastname": "Kowalski",
    "country_code": "PL",
    "city": "Warsaw",
    "postcode": "00-001",
    "street": "Main 1",
    "dialling_code": "+48",
    "telephone": "123456789",
}


@pytest.fixture
def stripe(channel):
    method = PaymentMethod.objects.create(
        channel=channel, code="stripe", provider=PaymentProvider.STRIPE, name_t9n={"en": "Card"}
    )
    method.countries.add(channel.default_country)
    method.currencies.add(channel.default_currency)
    return method


@pytest.fixture
def cart_with_address(channel, api_key):
    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    request = factory.patch(
        "/addresses/", {"billing_address": BUYER, "shipping_address": BUYER}, format="json", HTTP_X_API_KEY=api_key.key
    )
    assert CartAddressesView.as_view()(request, channel_idx=channel.idx, cart_id=str(record.cart_id)).status_code == 200
    record.refresh_from_db()
    return record


def _patch_payment(api_key, channel, cart, body):
    request = factory.patch("/payment/", body, format="json", HTTP_X_API_KEY=api_key.key)
    return CartPaymentView.as_view()(request, channel_idx=channel.idx, cart_id=str(cart.cart_id))


def _stored_entry(cart):
    cart.refresh_from_db()
    return cart.cart_body["payment_method"][0]


@pytest.fixture
def bridge(monkeypatch):
    """django_checkout.settings binds its constants at import time: patch the module."""

    def configure(direct, schemes, hosts):
        monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_DIRECT_SCHEMES", direct)
        monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES", schemes)
        monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_ALLOWED_HOSTS", hosts)

    return configure


@pytest.mark.django_db
@pytest.mark.usefixtures("stripe")
class TestTheReturnUrlIsStored:
    def test_as_v1_stores_it(self, api_key, channel, cart_with_address):
        response = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": RETURN_URL})

        assert response.status_code == 200
        assert _stored_entry(cart_with_address)["continue_url"] == RETURN_URL

    def test_the_gateway_gets_it_with_the_order_number(self, api_key, channel, cart_with_address, stripe):
        _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": RETURN_URL})
        cart_with_address.refresh_from_db()
        order = type("Order", (), {"pretty_id": "0200000042"})()

        url = BasePaymentProvider(stripe).resolve_own_continue_url(order, cart_with_address.as_data)

        assert url == "https://shop.example.org/checkout/success?ref=0200000042"

    def test_it_is_never_returned(self, api_key, channel, cart_with_address):
        response = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": RETURN_URL})

        assert "continue_url" not in response.data["payment_method"]
        assert RETURN_URL not in str(response.data)

    def test_a_payment_patch_without_it_stores_none(self, api_key, channel, cart_with_address):
        """The PATCH sends the whole payment method, as v1's payment_method section does."""
        _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": RETURN_URL})

        _patch_payment(api_key, channel, cart_with_address, {"code": "stripe"})

        assert _stored_entry(cart_with_address)["continue_url"] is None


@pytest.mark.django_db
@pytest.mark.usefixtures("stripe")
class TestTheTargetIsChecked:
    @pytest.mark.parametrize(
        "url",
        ["http://shop.example.org/checkout/success", "javascript:alert(1)", "myapp://paid", "//shop.example.org/x"],
    )
    def test_a_target_the_payment_step_refuses_is_a_400(self, api_key, channel, cart_with_address, url):
        response = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": url})

        assert response.status_code == 400
        assert [detail["field"] for detail in response.data["details"]] == ["continue_url"]
        cart_with_address.refresh_from_db()
        assert not cart_with_address.cart_body.get("payment_method")

    def test_a_bridged_target_needs_an_allowed_host(self, api_key, channel, cart_with_address, bridge):
        bridge(direct=[], schemes=["https"], hosts=["shop.example.org"])

        allowed = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": RETURN_URL})
        refused = _patch_payment(
            api_key, channel, cart_with_address, {"code": "stripe", "continue_url": "https://evil.example.com/"}
        )

        assert (allowed.status_code, refused.status_code) == (200, 400)
        assert _stored_entry(cart_with_address)["continue_url"] == RETURN_URL

    def test_a_native_app_scheme_the_bridge_allows(self, api_key, channel, cart_with_address, bridge):
        bridge(direct=["https"], schemes=["https", "myapp"], hosts=[])

        response = _patch_payment(
            api_key, channel, cart_with_address, {"code": "stripe", "continue_url": "myapp://paid"}
        )

        assert response.status_code == 200
        assert _stored_entry(cart_with_address)["continue_url"] == "myapp://paid"


@pytest.mark.django_db
@pytest.mark.usefixtures("stripe")
class TestTheValueStored:
    def test_surrounding_spaces_are_not_sent_to_the_gateway(self, api_key, channel, cart_with_address):
        _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": f"  {RETURN_URL} "})

        assert _stored_entry(cart_with_address)["continue_url"] == RETURN_URL

    def test_an_empty_one_is_no_return_url(self, api_key, channel, cart_with_address):
        response = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": ""})

        assert response.status_code == 200
        assert _stored_entry(cart_with_address)["continue_url"] is None

    def test_it_is_bounded(self, api_key, channel, cart_with_address):
        url = "https://shop.example.org/" + "a" * 2048

        response = _patch_payment(api_key, channel, cart_with_address, {"code": "stripe", "continue_url": url})

        assert response.status_code == 400


def _payment_step_accepts(url) -> bool:
    """What ``resolve_continue_url`` decides at the order, without an order or a bridge row."""
    provider = BasePaymentProvider(SimpleNamespace(channel=SimpleNamespace(idx="test-channel")))
    provider.request = SimpleNamespace(build_absolute_uri=lambda path: path)
    with (
        patch("django_checkout.models.PaymentRedirect"),
        patch("django_checkout.domain.payment_provider.base_payment_provider.reverse", return_value="/bridge/"),
    ):
        try:
            provider.resolve_continue_url(url, order=None)
        except ValueError:
            return False
    return True


@pytest.mark.parametrize(
    ("direct", "schemes", "hosts"),
    [(["https"], ["https"], []), ([], ["https"], ["shop.example.org"]), (["https"], ["https", "myapp"], [])],
)
@pytest.mark.parametrize(
    "url",
    [
        RETURN_URL,
        "https://evil.example.com/",
        "https://evil.example.com\\@shop.example.org/",
        "http://shop.example.org/",
        "myapp://paid",
        "javascript:alert(1)",
        "//shop.example.org/",
    ],
)
def test_the_patch_accepts_what_the_payment_step_accepts(bridge, direct, schemes, hosts, url):
    from django_checkout.domain.payment_redirect_target import is_allowed_continue_url

    bridge(direct=direct, schemes=schemes, hosts=hosts)

    assert is_allowed_continue_url(url) is _payment_step_accepts(url)


def test_the_openapi_request_carries_it():
    patterns = [path("api/checkout/v2/<str:channel_idx>/", include("django_checkout.api.v2.urls"))]
    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    request = schema["paths"]["/api/checkout/v2/{channel_idx}/carts/{cart_id}/payment/"]["patch"]["requestBody"]
    ref = request["content"]["application/json"]["schema"]["$ref"]  # PATCH: drf-spectacular names it Patched...
    model = schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    assert "continue_url" not in model.get("required", [])
    assert "<order_id>" in model["properties"]["continue_url"]["description"]
