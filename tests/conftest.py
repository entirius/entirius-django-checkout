# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import secrets

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from django_checkout.utils.api_keys import ERASE_SCOPE, STOREFRONT_SCOPE  # noqa: F401 — the helpers' vocabulary

User = get_user_model()


def import_keys_into_access() -> None:
    """With django_access installed keys are tokens: import the legacy rows as a deploy's migrate does."""
    if apps.is_installed("django_access"):
        from django_access.services.legacy import import_legacy_keys

        import_legacy_keys()


@pytest.fixture
def api_client():
    """Unauthenticated API client."""
    return APIClient()


@pytest.fixture
def admin_user(db):
    return User.objects.create_superuser(username="admin", password="admin123", email="admin@test.com")


@pytest.fixture
def admin_client(api_client, admin_user):
    """API client authenticated as admin (JWT)."""
    api_client.force_authenticate(user=admin_user)
    return api_client


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(username="customer", password="customer123", email="customer@test.com")


@pytest.fixture
def regular_client(api_client, regular_user):
    """API client authenticated as non-admin user."""
    api_client.force_authenticate(user=regular_user)
    return api_client


@pytest.fixture
def default_language(db):
    """A django_regional Language (Channel.default_language is NOT NULL)."""
    from django_regional.models import Language

    language, _ = Language.objects.get_or_create(
        iso2="en", defaults={"iso3": "eng", "name_en": "English", "name_pl": "Angielski"}
    )
    return language


@pytest.fixture
def default_currency(db):
    """A django_regional Currency (Channel.default_currency is NOT NULL)."""
    from django_regional.models import Currency

    currency, _ = Currency.objects.get_or_create(iso3="EUR", defaults={"name_en": "Euro", "name_pl": "Euro"})
    return currency


@pytest.fixture
def default_country(db):
    """A django_regional Country (Channel.default_country is NOT NULL).

    Country.save() is intentionally blocked (seed model), so insert via bulk_create.
    """
    from django_regional.models import Country

    country = Country.objects.filter(iso2="PL").first()
    if country is None:
        Country.objects.bulk_create([Country(iso2="PL", iso3="POL", name_en="Poland", name_pl="Polska")])
        country = Country.objects.get(iso2="PL")
    return country


@pytest.fixture
def channel(db, default_language, default_currency, default_country):
    """Create a checkout Channel with basic config."""
    from django_checkout.models import Channel

    return Channel.objects.create(
        idx="test-channel",
        label="Test Channel",
        min_order_price=0,
        default_language=default_language,
        default_currency=default_currency,
        default_country=default_country,
    )


@pytest.fixture
def other_channel(channel):
    """A second channel with the first one's defaults."""
    from django_checkout.models import Channel

    return Channel.objects.create(
        idx="other-channel",
        label="Other Channel",
        min_order_price=0,
        default_language=channel.default_language,
        default_currency=channel.default_currency,
        default_country=channel.default_country,
    )


@pytest.fixture
def cart(channel):
    from django_checkout.services import cart_service

    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    return record


@pytest.fixture
def api_key(channel):
    """Create an APIKey for the test channel."""
    from django_checkout.models import APIKey

    key = APIKey(channel=channel)
    key.save()
    import_keys_into_access()
    return key


@pytest.fixture
def auth_api_client(api_client, api_key):
    """API client with X-API-KEY header set (channel auth)."""
    api_client.credentials(HTTP_X_API_KEY=api_key.key)
    return api_client


@pytest.fixture
def customer(db, regular_user):
    """django_accounts Customer for regular_user.

    The v2 customer endpoints resolve the caller through ``request.user.customer``,
    so an authenticated user without this row is treated as anonymous.
    """
    from django_accounts.models import Customer

    return Customer.objects.create(user=regular_user)


@pytest.fixture
def auth_customer_client(api_client, api_key, regular_user):
    """API client with both X-API-KEY and JWT auth (authenticated customer)."""
    api_client.credentials(HTTP_X_API_KEY=api_key.key)
    api_client.force_authenticate(user=regular_user)
    return api_client


@pytest.fixture
def make_api_key(db):
    """Create a key the module accepts today and return its raw value.

    ``scope`` picks the table: ``ERASE_SCOPE`` → ``APIAdminKey`` (X-API-ADMIN-KEY), anything else → ``APIKey``
    (X-API-KEY). With django_access installed the key is also imported as a legacy token. The key contract tests go
    through this helper only, so moving the checks onto another key store
    changes this function, never the assertions. Values are random and never printed.
    """
    from django_checkout.models import APIAdminKey, APIKey

    def make_api_key(channel=None, scope: str | None = None) -> str:
        model = APIAdminKey if scope == ERASE_SCOPE else APIKey
        raw = secrets.token_hex(32)
        model.objects.create(channel=channel, key=raw)
        import_keys_into_access()
        return raw

    return make_api_key
