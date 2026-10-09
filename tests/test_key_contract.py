# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Characterization of today's checkout key contract (X-API-KEY and X-API-ADMIN-KEY).

Pins what the keyed routes answer — quirks included — so moving the key checks onto another key store
cannot change a status, a body or a side contract. Keys come only from the ``make_api_key`` helper.

The module has no ROOT_URLCONF, so views are called directly.
"""

import json
import secrets
from unittest.mock import patch

import pytest
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from django_checkout.api.v2.views.cart_views import CountriesView
from django_checkout.views.admin.customer import admin_customer_delete
from django_checkout.views.cart import get_or_put_cart
from django_checkout.views.countries import listing_view
from tests.conftest import ERASE_SCOPE

factory = APIRequestFactory()


@pytest.fixture(autouse=True)
def _live_keys(channel, make_api_key):
    """Valid keys exist in every test, so a refusal proves the lookup, not an empty key store."""
    make_api_key(channel=channel)
    make_api_key(channel=channel, scope=ERASE_SCOPE)


@pytest.fixture
def customer_jwt(customer):
    return str(RefreshToken.for_user(customer.user).access_token)


def _wrong_key() -> str:
    return secrets.token_hex(32)


def _headers(key: str | None, header: str = "HTTP_X_API_KEY") -> dict:
    return {} if key is None else {header: key}


def _body(response) -> dict:
    return json.loads(response.content)


def _v1_refusal(response) -> tuple[int, str, str]:
    """The v1 envelope puts the refusal text in ``data``, not in ``meta.message`` (quirk: positional arg)."""
    body = _body(response)
    return response.status_code, body["meta"]["status"], body["data"]


# --- v1 storefront (@channel_view): every refusal is 400 "Invalid api key" (quirk: not 401) ---


def _v1_cart(cart, key: str | None, channel_idx: str = "test-channel"):
    request = factory.get("/", **_headers(key))
    return get_or_put_cart(request, channel_idx=channel_idx, cart_id=str(cart.cart_id), version="1")


@pytest.mark.django_db
class TestV1ChannelView:
    def test_missing_key_is_400(self, cart):
        assert _v1_refusal(_v1_cart(cart, None)) == (400, "BAD_REQUEST", "Invalid api key")

    def test_wrong_key_is_400(self, cart):
        assert _v1_refusal(_v1_cart(cart, _wrong_key())) == (400, "BAD_REQUEST", "Invalid api key")

    def test_other_channel_key_is_refused_like_a_wrong_key(self, cart, other_channel, make_api_key):
        wrong = _v1_cart(cart, _wrong_key())
        foreign_key = make_api_key(channel=other_channel)
        foreign = _v1_cart(cart, foreign_key)
        assert foreign.status_code == wrong.status_code == 400
        assert _body(foreign) == _body(wrong)
        assert foreign_key not in foreign.content.decode()

    def test_admin_key_is_not_a_storefront_key(self, cart, channel, make_api_key):
        wrong = _v1_cart(cart, _wrong_key())
        admin_key = make_api_key(channel=channel, scope=ERASE_SCOPE)
        refused = _v1_cart(cart, admin_key)
        assert _v1_refusal(refused) == _v1_refusal(wrong)
        assert admin_key not in refused.content.decode()

    def test_unknown_channel_is_404_before_the_key(self, cart, channel, make_api_key):
        response = _v1_cart(cart, make_api_key(channel=channel), channel_idx="no-such-channel")
        assert response.status_code == 404

    def test_right_key_passes(self, cart, channel, make_api_key):
        response = _v1_cart(cart, make_api_key(channel=channel))
        assert response.status_code == 200
        assert _body(response)["data"]["cart_id"] == str(cart.cart_id)


# --- v2 storefront (ChannelAPIKeyPermission on JWTAuthentication views) ---


def _v2_countries(key: str | None, jwt: str | None = None, view=CountriesView):
    headers = _headers(key)
    if jwt:
        headers["HTTP_AUTHORIZATION"] = f"Bearer {jwt}"
    request = factory.get("/countries/", **headers)
    return view.as_view()(request, channel_idx="test-channel")


class _ChannelEchoView(CountriesView):
    """CountriesView's own auth stack, answering with the channel the key check left on the request."""

    def get(self, request, **kwargs):
        return Response({"checkout_channel": request.checkout_channel.idx})


@pytest.mark.django_db
class TestV2ChannelAPIKeyPermission:
    def test_missing_key_without_jwt_is_401(self, channel):
        assert _v2_countries(None).status_code == 401

    def test_wrong_key_without_jwt_is_401(self, channel):
        assert _v2_countries(_wrong_key()).status_code == 401

    def test_wrong_key_with_customer_jwt_is_403(self, channel, customer_jwt):
        assert _v2_countries(_wrong_key(), jwt=customer_jwt).status_code == 403

    def test_other_channel_key_is_refused_like_a_wrong_key(self, channel, other_channel, make_api_key):
        wrong = _v2_countries(_wrong_key())
        foreign_key = make_api_key(channel=other_channel)
        foreign = _v2_countries(foreign_key)
        foreign.render()
        wrong.render()
        assert foreign.status_code == wrong.status_code == 401
        assert foreign.data == wrong.data
        assert foreign_key not in foreign.content.decode()

    def test_admin_key_is_not_a_storefront_key(self, channel, make_api_key):
        wrong = _v2_countries(_wrong_key())
        admin_key = make_api_key(channel=channel, scope=ERASE_SCOPE)
        refused = _v2_countries(admin_key)
        refused.render()
        assert refused.status_code == wrong.status_code == 401
        assert refused.data == wrong.data
        assert admin_key not in refused.content.decode()

    def test_right_key_is_200(self, channel, make_api_key):
        response = _v2_countries(make_api_key(channel=channel))
        assert response.status_code == 200
        assert response.data["default_country"]["code"] == "PL"

    def test_right_key_with_customer_jwt_is_200(self, channel, customer_jwt, make_api_key):
        assert _v2_countries(make_api_key(channel=channel), jwt=customer_jwt).status_code == 200

    def test_right_key_sets_checkout_channel(self, channel, make_api_key):
        response = _v2_countries(make_api_key(channel=channel), view=_ChannelEchoView)
        assert response.status_code == 200
        assert response.data == {"checkout_channel": channel.idx}


# --- X-API-ADMIN-KEY customer/delete ---

_ANONYMIZED = (True, ["order-1"], [], [], [])


def _admin_delete(key: str | None, channel_idx: str = "test-channel"):
    request = factory.delete(
        "/",
        data=json.dumps({"email": "erase@test.com"}),
        content_type="application/json",
        **_headers(key, "HTTP_X_API_ADMIN_KEY"),
    )
    with patch("django_checkout.views.admin.customer.CustomerService.anonymize_customer", return_value=_ANONYMIZED):
        return admin_customer_delete(request, channel_idx=channel_idx, version="1")


@pytest.mark.django_db
class TestAdminKeyCustomerDelete:
    def test_unknown_channel_is_404(self, channel, make_api_key):
        response = _admin_delete(make_api_key(channel=channel, scope=ERASE_SCOPE), channel_idx="no-such-channel")
        assert response.status_code == 404

    def test_missing_key_is_401(self, channel):
        assert _v1_refusal(_admin_delete(None)) == (401, "UNAUTHORIZED", "Invalid api admin key")

    def test_wrong_key_is_401(self, channel):
        assert _v1_refusal(_admin_delete(_wrong_key())) == (401, "UNAUTHORIZED", "Invalid api admin key")

    def test_other_channel_key_is_refused_like_a_wrong_key(self, channel, other_channel, make_api_key):
        wrong = _admin_delete(_wrong_key())
        foreign_key = make_api_key(channel=other_channel, scope=ERASE_SCOPE)
        foreign = _admin_delete(foreign_key)
        assert foreign.status_code == wrong.status_code == 401
        assert _body(foreign) == _body(wrong)
        assert foreign_key not in foreign.content.decode()

    def test_storefront_key_is_not_an_admin_key(self, channel, make_api_key):
        wrong = _admin_delete(_wrong_key())
        storefront_key = make_api_key(channel=channel)
        refused = _admin_delete(storefront_key)
        assert _v1_refusal(refused) == _v1_refusal(wrong)
        assert storefront_key not in refused.content.decode()

    def test_right_key_anonymizes(self, channel, make_api_key):
        response = _admin_delete(make_api_key(channel=channel, scope=ERASE_SCOPE))
        assert response.status_code == 200
        assert _body(response)["data"] == {"deleted": True, "orders": ["order-1"], "carts": []}


# --- public routes that take no key ---


@pytest.mark.django_db
def test_v1_countries_answers_without_a_key(channel):
    response = listing_view(factory.get("/"), channel_idx=channel.idx, version="1")
    assert response.status_code == 200
