# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The v2 OpenAPI schema offers a guest route without the customer JWT; a customer route still needs it.

drf-spectacular adds the anonymous alternative (an empty requirement) when a view's permissions include AllowAny;
the key hook of django_access then turns it into the channel key alone."""

import pytest
from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator

PATTERNS = [path("api/checkout/v2/<str:channel_idx>/", include("django_checkout.api.v2.urls"))]
PREFIX = "/api/checkout/v2/{channel_idx}/"

GUEST = [
    ("post", "carts/"),
    ("get", "carts/{cart_id}/"),
    ("delete", "carts/{cart_id}/"),
    ("patch", "carts/{cart_id}/items/"),
    ("patch", "carts/{cart_id}/addresses/"),
    ("patch", "carts/{cart_id}/discounts/"),
    ("get", "carts/{cart_id}/shipping-methods/"),
    ("patch", "carts/{cart_id}/shipping/"),
    ("get", "carts/{cart_id}/payment-methods/"),
    ("patch", "carts/{cart_id}/payment/"),
    ("get", "carts/{cart_id}/gratis-rules/"),
    ("get", "countries/"),
    ("post", "orders/"),
]
CUSTOMER = [
    ("get", "carts/latest/"),
    ("post", "carts/{cart_id}/merge/"),
    ("get", "orders/list/"),
    ("get", "orders/{pretty_id}/"),
    ("get", "orders/{order_id}/files/{file_id}/"),
]


@pytest.fixture(scope="module")
def paths():
    return SchemaGenerator(patterns=PATTERNS).get_schema(request=None, public=True)["paths"]


@pytest.mark.parametrize(("method", "route"), GUEST)
def test_a_guest_route_does_not_require_the_jwt(paths, method, route):
    assert {} in paths[PREFIX + route][method]["security"]


@pytest.mark.parametrize(("method", "route"), CUSTOMER)
def test_a_customer_route_requires_the_jwt(paths, method, route):
    assert {} not in paths[PREFIX + route][method].get("security", [])
