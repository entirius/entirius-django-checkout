# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Cart sub-resource endpoint tests (shipping / payment / gratis / countries).

These endpoints previously returned empty payloads: the v2 views imported
``_build_*_response`` helpers that did not exist, and the resulting ImportError
was swallowed by a broad ``try/except → []``. These tests assert the endpoints
now return the real, built data.

Views are called directly via ``APIRequestFactory`` (the module has no
ROOT_URLCONF); channel auth is supplied through the ``X-API-KEY`` header.
"""

from decimal import Decimal
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from django_checkout.api.v2.views.cart_views import (
    CartAddressesView,
    CartGratisView,
    CartPaymentMethodsView,
    CartShippingMethodsView,
    CountriesView,
)
from django_checkout.services import cart_service

factory = APIRequestFactory()


def _key_call(view, path, api_key, **kwargs):
    request = factory.get(path, HTTP_X_API_KEY=api_key.key)
    return view.as_view()(request, **kwargs)


@pytest.fixture
def cart_with_address(channel, api_key):
    """A cart with billing + shipping addresses set through the real v2 view.

    Routing through CartAddressesView (not cart_service directly) is deliberate:
    the Pydantic AddressInput schema always injects `tax_id: None`, which is what
    previously caused shipping addresses to be stored in a billing shape that
    `Cart.as_data` could not reload. This fixture reproduces that real path so the
    method-endpoint tests below regression-guard it.
    """
    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    address = {
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
    request = factory.patch(
        "/addresses/",
        {"billing_address": address, "shipping_address": address},
        format="json",
        HTTP_X_API_KEY=api_key.key,
    )
    response = CartAddressesView.as_view()(request, channel_idx=channel.idx, cart_id=str(record.cart_id))
    assert response.status_code == 200
    record.refresh_from_db()
    return record


def test_as_data_roundtrips_with_shipping_address(cart_with_address):
    # Regression: a stored shipping address must reload via Cart.as_data (strict
    # marshmallow). Previously it was stored billing-shaped and threw on reload,
    # 500-ing every method endpoint once an address was set.
    from django_checkout.domain.dto.address import Address, BillingAddress

    data = cart_with_address.as_data
    assert isinstance(data.addresses.shipping_address, Address)
    assert isinstance(data.addresses.billing_address, BillingAddress)


def test_shipping_methods_returns_available(channel, api_key, cart_with_address):
    from django_checkout.models import ShippingMethod, ShippingOption

    method = ShippingMethod.objects.create(channel=channel, code="dhl", name_t9n={"en": "DHL"})
    # ShippingOption.save() calls cache.delete_pattern() (django-redis); the test
    # settings use LocMemCache which lacks it, so no-op the cache on create.
    with patch("django_checkout.models.shipping_option.cache"):
        ShippingOption.objects.create(
            method=method,
            currency=channel.default_currency,
            country_code="ALL",
            price_brutto=Decimal("10.00"),
            tax_rate=Decimal("0.23"),
        )
    response = _key_call(
        CartShippingMethodsView,
        "/shipping-methods/",
        api_key,
        channel_idx=channel.idx,
        cart_id=str(cart_with_address.cart_id),
    )
    assert response.status_code == 200
    assert "dhl" in [m["code"] for m in response.data]


def test_payment_methods_returns_available(channel, api_key, cart_with_address):
    from django_checkout.enums import PaymentProvider
    from django_checkout.models import PaymentMethod

    method = PaymentMethod.objects.create(channel=channel, code="transfer", provider=PaymentProvider.TRANSFER)
    method.countries.add(channel.default_country)
    method.currencies.add(channel.default_currency)
    response = _key_call(
        CartPaymentMethodsView,
        "/payment-methods/",
        api_key,
        channel_idx=channel.idx,
        cart_id=str(cart_with_address.cart_id),
    )
    assert response.status_code == 200
    assert "transfer" in [m["code"] for m in response.data]


def test_countries_returns_channel_countries(channel, api_key, default_language, default_country):
    channel.languages.add(default_language)
    channel.countries.add(default_country)
    response = _key_call(CountriesView, "/countries/?language=en", api_key, channel_idx=channel.idx)
    assert response.status_code == 200
    assert response.data["default_country"] is not None
    assert response.data["default_country"]["code"] == "PL"
    assert any(c["code"] == "PL" for c in response.data["countries"])


def test_gratis_rules_returns_ok(channel, api_key, cart_with_address):
    # No gratis rules configured -> empty list, but the endpoint must actually run
    # the helper (previously any exception, incl. ImportError, was swallowed to []).
    # A clean 200 + list proves the helper is wired and executes.
    response = _key_call(
        CartGratisView,
        "/gratis-rules/",
        api_key,
        channel_idx=channel.idx,
        cart_id=str(cart_with_address.cart_id),
    )
    assert response.status_code == 200
    assert isinstance(response.data, list)
