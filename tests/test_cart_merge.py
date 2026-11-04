# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Merging a guest cart into the customer's cart, v1 and v2.

Both merge paths built their request with ``CartRequest.factory()``, which left out five fields the dataclass
requires (``custom_order_id``, ``requested_delivery_date``, ``split_order``, ``affiliate_code``, ``customer_ip``).
Every merge raised ``TypeError`` before touching a cart: v2 answered 500, v1 its generic error.

A gift line is not something the customer bought: the merge sums the bought lines only and the gift comes back
from its code, so a gift never turns into a paid unit. Only an open guest cart merges: one that became an order is
protected by it, and deleting it after the merge raised ``ProtectedError`` (v2 500).

Views are called directly (the module has no ROOT_URLCONF).
"""

import json

import pytest
from django.test import RequestFactory
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import RefreshToken

from django_checkout.api.v2.views.cart_views import CartMergeView
from django_checkout.domain.dto.cart import CartRequest
from django_checkout.enums import CartStatus, OrderStatus
from django_checkout.models import Cart, Order
from django_checkout.services import cart_service
from django_checkout.views.cart import merge_cart
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _claim_the_gift,
    _stock,
    catalog,
    coffee_in_stock,
    gratis_code,
    gratis_rule,
    pim_channel,
    priced_catalog,
    supplier,
)


@pytest.fixture
def shop(channel, supplier, priced_catalog, coffee_in_stock):  # noqa: F811
    """Coffee (123.00) and mugs (24.60) priced and in stock."""
    _stock(channel, supplier, GIFT_SKU, quantity=5)
    return channel


@pytest.fixture
def gift_shop(shop, gratis_code):  # noqa: F811
    """The same shop, and a mug for free with the coffee (code ENT-MUG)."""
    return shop


def _cart(channel, items, customer=None):
    record, _ = cart_service.create_cart(
        channel=channel,
        items=[{"sku": sku, "quantity": quantity} for sku, quantity in items],
        currency_code="EUR",
        language_code="en",
        country_code="PL",
        customer=customer,
    )
    return record


def _ordered(guest):
    """The guest checked out before logging in: the cart is confirmed and an order points at it."""
    guest.cart_status = CartStatus.CONFIRMED
    guest.save()
    return Order.objects.create(channel=guest.channel, cart=guest, order_status=OrderStatus.UNPAID, order_body={})


def _lines(record) -> list[tuple[str, int, bool]]:
    return [(item.sku, int(item.quantity), bool(item.is_gratis)) for item in record.as_data.cart.items]


def _merge_v2(api_key, channel, user, target, guest, operation="add"):
    request = APIRequestFactory().post(
        "/", {"guest_cart_id": str(guest.cart_id), "operation": operation}, format="json", HTTP_X_API_KEY=api_key.key
    )
    force_authenticate(request, user=user)
    return CartMergeView.as_view()(request, channel_idx=channel.idx, cart_id=str(target.cart_id))


def _merge_v1(api_key, channel, user, guest, operation="add"):
    token = str(RefreshToken.for_user(user).access_token)
    request = RequestFactory().post(
        f"/?operation_type={operation}", HTTP_X_API_KEY=api_key.key, HTTP_AUTHORIZATION=f"Bearer {token}"
    )
    return merge_cart(request, channel_idx=channel.idx, quest_cart_id=str(guest.cart_id))


def test_the_empty_request_builds():
    """The merge body starts from the factory: it must give every field the dataclass requires."""
    body = CartRequest.factory()

    assert body.cart.items == []
    assert (body.custom_order_id, body.requested_delivery_date, body.affiliate_code, body.customer_ip) == (
        None,
        None,
        None,
        None,
    )
    assert body.split_order is None


@pytest.mark.django_db
class TestV2Merge:
    def test_add_sums_the_guest_items_into_the_customer_cart(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(TRIGGER_SKU, 1), (GIFT_SKU, 1)])

        response = _merge_v2(api_key, channel, customer.user, target, guest)

        assert response.status_code == 200
        assert [(i["sku"], i["quantity"]) for i in response.data["items"]] == [(TRIGGER_SKU, 3), (GIFT_SKU, 1)]
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 3, False), (GIFT_SKU, 1, False)]
        assert not Cart.objects.filter(pk=guest.pk).exists()

    def test_override_keeps_only_the_guest_items(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(GIFT_SKU, 1)])

        response = _merge_v2(api_key, channel, customer.user, target, guest, operation="override")

        assert response.status_code == 200
        target.refresh_from_db()
        assert _lines(target) == [(GIFT_SKU, 1, False)]

    def test_a_missing_guest_cart_is_a_404(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 1)], customer=customer)
        guest = _cart(channel, [(TRIGGER_SKU, 1)])
        guest.delete()

        response = _merge_v2(api_key, channel, customer.user, target, guest)

        assert response.status_code == 404

    def test_a_guest_cart_that_became_an_order_is_a_404(self, api_key, channel, shop, customer):
        """The order protects the cart: deleting it after the merge raised ProtectedError, a 500."""
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(GIFT_SKU, 1)])
        order = _ordered(guest)

        response = _merge_v2(api_key, channel, customer.user, target, guest)

        assert response.status_code == 404
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 2, False)]
        assert Order.objects.filter(pk=order.pk, cart=guest).exists()

    def test_the_customers_gift_stays_a_gift(self, api_key, channel, gift_shop, customer):
        """The gift line is not summed as a bought mug; the customer's code puts the gift back."""
        target = _claim_the_gift(channel, customer=customer)
        assert _lines(target) == [(TRIGGER_SKU, 1, False), (GIFT_SKU, 1, True)]
        guest = _cart(channel, [(TRIGGER_SKU, 1)])

        response = _merge_v2(api_key, channel, customer.user, target, guest)

        assert response.status_code == 200
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 2, False), (GIFT_SKU, 1, True)]

    def test_the_guests_gift_is_not_bought_on_merge(self, api_key, channel, gift_shop, customer):
        """The guest's codes do not follow the merge (docs/api-v2-plan.md), so neither does the gift line."""
        target = _cart(channel, [(TRIGGER_SKU, 1)], customer=customer)
        guest = _claim_the_gift(channel)
        assert _lines(guest) == [(TRIGGER_SKU, 1, False), (GIFT_SKU, 1, True)]

        response = _merge_v2(api_key, channel, customer.user, target, guest)

        assert response.status_code == 200
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 2, False)]


@pytest.mark.django_db
class TestV1Merge:
    def test_add_sums_the_guest_items_into_the_customer_cart(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(TRIGGER_SKU, 1), (GIFT_SKU, 1)])

        response = _merge_v1(api_key, channel, customer.user, guest)

        assert response.status_code == 200, response.content
        items = json.loads(response.content)["data"]["cart"]["items"]
        assert [(i["sku"], i["quantity"]) for i in items] == [(TRIGGER_SKU, "3"), (GIFT_SKU, "1")]
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 3, False), (GIFT_SKU, 1, False)]
        assert not Cart.objects.filter(pk=guest.pk).exists()

    def test_override_keeps_only_the_guest_items(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(GIFT_SKU, 1)])

        response = _merge_v1(api_key, channel, customer.user, guest, operation="override")

        assert response.status_code == 200, response.content
        target.refresh_from_db()
        assert _lines(target) == [(GIFT_SKU, 1, False)]

    def test_the_customers_gift_stays_a_gift(self, api_key, channel, gift_shop, customer):
        target = _claim_the_gift(channel, customer=customer)
        guest = _cart(channel, [(TRIGGER_SKU, 1)])

        response = _merge_v1(api_key, channel, customer.user, guest)

        assert response.status_code == 200, response.content
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 2, False), (GIFT_SKU, 1, True)]

    def test_a_guest_cart_that_became_an_order_is_a_404(self, api_key, channel, shop, customer):
        target = _cart(channel, [(TRIGGER_SKU, 2)], customer=customer)
        guest = _cart(channel, [(GIFT_SKU, 1)])
        _ordered(guest)

        response = _merge_v1(api_key, channel, customer.user, guest)

        assert response.status_code == 404, response.content
        target.refresh_from_db()
        assert _lines(target) == [(TRIGGER_SKU, 2, False)]
