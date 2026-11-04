# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""POST orders/ names payment_error in its schema; the body carries it only when the payment failed."""

from unittest.mock import patch

from django.urls import include, path
from drf_spectacular.generators import SchemaGenerator
from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.views.order_views import OrderCreateView
from django_checkout.services.order_service import OrderCreateResult

factory = APIRequestFactory()


def _post(channel, api_key, payment_failed):
    result = OrderCreateResult(order=None, redirect_url=None, payment_failed=payment_failed, split_pretty_ids=[])
    with patch("django_checkout.services.order_service.create_order", return_value=result):
        request = factory.post("/orders/", {"cart_id": "c1"}, format="json", HTTP_X_API_KEY=api_key.key)
        return OrderCreateView.as_view()(request, channel_idx=channel.idx)


def test_a_failed_payment_says_so(channel, api_key):
    response = _post(channel, api_key, payment_failed=True)

    assert response.status_code == 201
    assert response.data["payment_error"] is True


def test_a_paid_order_has_no_payment_error(channel, api_key):
    response = _post(channel, api_key, payment_failed=False)

    assert response.status_code == 201
    assert "payment_error" not in response.data


def test_the_openapi_schema_lists_payment_error_as_an_optional_boolean():
    patterns = [path("api/checkout/v2/<str:channel_idx>/", include("django_checkout.api.v2.urls"))]
    schema = SchemaGenerator(patterns=patterns).get_schema(request=None, public=True)
    created = schema["paths"]["/api/checkout/v2/{channel_idx}/orders/"]["post"]["responses"]["201"]
    assert created["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/OrderCreateResponse"}

    model = schema["components"]["schemas"]["OrderCreateResponse"]

    assert model["properties"]["payment_error"]["type"] == "boolean"
    assert model["properties"]["payment_error"]["default"] is False
    assert "payment_error" not in model.get("required", [])
