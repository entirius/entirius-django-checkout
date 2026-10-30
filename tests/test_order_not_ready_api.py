# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 ``POST orders/`` on a cart that cannot become an order yet answers 409 with the reason.

``OrderValidation`` raises a v1 exception. The v2 exception handler does not know it, so these carts answered 500
``INTERNAL_ERROR``: a customer whose item sold out during checkout got no reason, and neither did the storefront.

Views are called directly via ``APIRequestFactory`` (the module has no ROOT_URLCONF).
"""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django_utils.api.exceptions import Forbidden
from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.views.cart_views import CartAddressesView
from django_checkout.api.v2.views.order_views import OrderCreateView
from django_checkout.domain.validators.order import CartNotReady
from django_checkout.models import Order
from django_checkout.services import cart_service

factory = APIRequestFactory()

ADDRESS = {
    "email": "buyer@test.com",
    "firstname": "Jan",
    "lastname": "Kowalski",
    "country_code": "PL",
    "city": "Warsaw",
    "postcode": "00-001",
    "street": "Main 1",
    "dialling_code": "+48",
    "telephone": "123456789",
}


def place_order(api_key, channel, cart_id):
    request = factory.post("/orders/", {"cart_id": str(cart_id)}, format="json", HTTP_X_API_KEY=api_key.key)
    return OrderCreateView.as_view()(request, channel_idx=channel.idx)


@pytest.fixture
def empty_cart(channel):
    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    return record


def test_a_cart_without_addresses(api_key, channel, empty_cart):
    response = place_order(api_key, channel, empty_cart.cart_id)

    assert response.status_code == 409
    assert response.data["error"] == "ADDRESSES_NOT_SELECTED"
    assert str(empty_cart.cart_id) in response.data["message"]
    assert response.data["debug_id"]
    assert response.data["details"] == []
    assert not Order.objects.exists()


def test_a_cart_without_a_shipping_method(api_key, channel, empty_cart):
    request = factory.patch(
        "/addresses/",
        {"billing_address": ADDRESS, "shipping_address": ADDRESS},
        format="json",
        HTTP_X_API_KEY=api_key.key,
    )
    response = CartAddressesView.as_view()(request, channel_idx=channel.idx, cart_id=str(empty_cart.cart_id))
    assert response.status_code == 200

    response = place_order(api_key, channel, empty_cart.cart_id)

    assert response.status_code == 409
    assert response.data["error"] == "SHIPPING_OPTION_NOT_SELECTED"
    assert not Order.objects.exists()


@pytest.mark.parametrize(
    ("missing", "error"),
    [("payment_method", "PAYMENT_OPTION_NOT_SELECTED"), (None, "OUT_OF_STOCK")],
    ids=["payment", "out_of_stock"],
)
def test_the_reason_comes_as_a_code(api_key, channel, empty_cart, missing, error):
    item = SimpleNamespace(sku="SKU-1", status="out_of_stock" if missing is None else "valid", total_price=1)
    processed = SimpleNamespace(
        cart=SimpleNamespace(items=[item]), payment_method=object(), shipping_method=object(), addresses=object()
    )
    if missing:
        setattr(processed, missing, None)

    with patch("django_checkout.services.order_service.process_cart", return_value=(processed, [])):
        response = place_order(api_key, channel, empty_cart.cart_id)

    assert response.status_code == 409
    assert response.data["error"] == error
    if missing is None:
        assert "SKU-1" in response.data["message"]


def test_an_unknown_cart_stays_not_found(api_key, channel):
    assert place_order(api_key, channel, uuid4()).status_code == 404


def test_the_v1_view_still_catches_it():
    assert issubclass(CartNotReady, Forbidden)
