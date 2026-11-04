# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""A voucher on a cart that splits into several orders is refused, on the cart and at the order.

Each part of a split is created with one payment method, the gateway (``settling_payment_method``): the voucher
reaches none of the parts. The checkout showed the total net of the voucher, the parts added up to the full amount,
and the voucher kept its balance. Until a voucher can be shared between the parts, the cart says so while the
customer can still drop the voucher or the items that split it, and ``POST orders/`` (v1 and v2) refuses it.

The split itself needs PIM features; the tests answer ``check_that_order_can_be_split`` with the part count.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django_utils.api.responses import ErrorInfo
from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.views.order_views import OrderCreateView
from django_checkout.services import cart_service
from django_checkout.views.order import order_view
from tests.test_voucher_rejection_reasons import (  # noqa: F401 - fixtures
    _RejectingVoucherProvider,
    cart_awaiting_payment,
    priced_product,
    voucher_method,
)

SPLITTER = "django_checkout.domain.cart.check_that_order_can_be_split"
VOUCHER_ON_SPLIT_ORDER = "voucher_not_supported_on_split_order"
CART = SimpleNamespace(cart_id="c1")
CHANNEL = SimpleNamespace(idx="test-channel")


def _split_parts(cart, carries_voucher):
    from django_checkout.domain.cart import voucher_split_parts

    return voucher_split_parts(cart, CHANNEL, carries_voucher=carries_voucher)


class TestTheRule:
    """It must fire on the intersection only: a false positive blocks every split, or every voucher checkout."""

    @pytest.mark.parametrize(("parts", "expected"), [(2, 2), (3, 3), (1, 0), (None, 0)])
    def test_a_cart_with_a_voucher_reports_the_parts_it_splits_into(self, parts, expected):
        with patch(SPLITTER, return_value=({}, parts)):
            assert _split_parts(CART, carries_voucher=True) == expected

    def test_a_cart_without_a_voucher_is_never_checked(self):
        with patch(SPLITTER, side_effect=AssertionError("a cart without a voucher needs no split check")):
            assert _split_parts(CART, carries_voucher=False) == 0

    def test_no_cart_record_is_no_split(self):
        with patch(SPLITTER, return_value=({}, 2)):
            assert _split_parts(None, carries_voucher=True) == 0


class _AcceptingVoucherProvider(_RejectingVoucherProvider):
    rejections = []


def _pay_with_voucher(channel, cart, parts):
    with (
        patch("django_checkout.models.payment_method.VoucherPaymentProvider", _AcceptingVoucherProvider),
        # create: before the fix the cart pipeline never asked whether the cart splits
        patch(SPLITTER, return_value=({}, parts), create=True),
    ):
        return cart_service.patch_payment(
            channel=channel, cart_id=str(cart.cart_id), code="voucher", pay_code="ENT-GIFT-1"
        )


def _codes(messages):
    return [getattr(message, "code", None) for message in messages]


@pytest.mark.django_db
@pytest.mark.usefixtures("voucher_method")
class TestTheCartSaysSo:
    def test_a_voucher_on_a_cart_that_splits(self, channel, cart_awaiting_payment):  # noqa: F811
        record, messages = _pay_with_voucher(channel, cart_awaiting_payment, parts=2)

        refusal = next(message for message in messages if getattr(message, "code", None) == VOUCHER_ON_SPLIT_ORDER)
        assert refusal.extra == {"split_parts": 2}
        assert record.as_data.validation_status == "invalid"

    def test_a_voucher_on_a_cart_that_does_not_split(self, channel, cart_awaiting_payment):  # noqa: F811
        _record, messages = _pay_with_voucher(channel, cart_awaiting_payment, parts=1)

        assert VOUCHER_ON_SPLIT_ORDER not in _codes(messages)


def _refusal(parts=2):
    """What ``process_cart`` reports for a voucher on a cart that splits."""
    return ErrorInfo(
        code=VOUCHER_ON_SPLIT_ORDER,
        message=f"This cart splits into {parts} orders and a voucher cannot pay for several orders.",
        affected_field="cart.payment_method",
        extra={"split_parts": parts},
    )


def _processed():
    return SimpleNamespace(
        cart=SimpleNamespace(items=[]), payment_method=object(), shipping_method=object(), addresses=object()
    )


class SplitReached(Exception):
    pass


@pytest.fixture
def splitting(monkeypatch):
    """The cart splits in two; the split records that it was reached and stops there."""
    reached = []

    def split(*args, **kwargs):
        reached.append("split")
        raise SplitReached

    for module in ("django_checkout.services.order_service", "django_checkout.views.order"):
        monkeypatch.setattr(f"{module}.check_that_order_can_be_split", lambda *args: ({}, 2))
        monkeypatch.setattr(f"{module}.split_orders_by_attribute", split)
    return reached


@pytest.fixture
def v2_errors(settings):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
    }


@pytest.mark.django_db
class TestTheOrderIsRefused:
    def test_v2_answers_409_with_the_reason(self, api_key, channel, cart, splitting, v2_errors):
        with patch(
            "django_checkout.services.order_service.process_cart",
            return_value=(_processed(), [_refusal()]),
        ):
            request = APIRequestFactory().post(
                "/orders/", {"cart_id": str(cart.cart_id)}, format="json", HTTP_X_API_KEY=api_key.key
            )
            response = OrderCreateView.as_view()(request, channel_idx=channel.idx)

        assert response.status_code == 409
        assert response.data["error"] == "VOUCHER_NOT_SUPPORTED_ON_SPLIT_ORDER"
        assert "2 orders" in response.data["message"]
        assert splitting == []

    def test_v1_answers_403_with_the_reason(self, api_key, channel, cart, splitting):
        with patch(
            "django_checkout.views.order.process_cart",
            return_value=(_processed(), [_refusal()]),
        ):
            request = RequestFactory().post(
                "/orders/",
                json.dumps({"cart_id": str(cart.cart_id)}),
                content_type="application/json",
                HTTP_X_API_KEY=api_key.key,
            )
            response = order_view(request, channel_idx=channel.idx)

        assert response.status_code == 403
        assert json.loads(response.content)["meta"]["status"] == "VOUCHER_NOT_SUPPORTED_ON_SPLIT_ORDER"
        assert splitting == []

    def test_a_split_without_a_voucher_still_goes_ahead(self, api_key, channel, cart, splitting):
        with patch("django_checkout.services.order_service.process_cart", return_value=(_processed(), [])):
            request = APIRequestFactory().post(
                "/orders/", {"cart_id": str(cart.cart_id)}, format="json", HTTP_X_API_KEY=api_key.key
            )
            with pytest.raises(SplitReached):
                OrderCreateView.as_view()(request, channel_idx=channel.idx)

        assert splitting == ["split"]
