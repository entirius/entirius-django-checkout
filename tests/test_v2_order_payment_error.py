# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""POST orders/ names payment_error in its schema; the body carries it only when the payment failed."""

from unittest.mock import patch

from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.views.order_views import OrderCreateView
from django_checkout.schemas.responses.order import OrderCreateResponse
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


def test_the_schema_lists_payment_error_as_optional():
    schema = OrderCreateResponse.model_json_schema()

    assert "payment_error" in schema["properties"]
    assert "payment_error" not in schema.get("required", [])
