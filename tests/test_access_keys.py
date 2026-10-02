# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The access path: with django_access installed every checkout key is an access token (``verify_api_key``).

Legacy keys reach it only through the import (``make_api_key``); the legacy tables are never read on this path.
"""

import json
import secrets
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

import pytest

pytest.importorskip("django_access")

from django.contrib import admin  # noqa: E402
from django.core.management import CommandError, call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.test.utils import CaptureQueriesContext  # noqa: E402
from django.utils import timezone  # noqa: E402
from django_access.models import ApiToken, Application  # noqa: E402
from django_access.services.access_service import Actor  # noqa: E402
from django_access.services.tokens import hash_key, issue_token, revoke_token, set_token_expiry  # noqa: E402
from rest_framework.test import APIRequestFactory  # noqa: E402

from django_checkout.api.v2.views.cart_views import CartDetailView, CountriesView  # noqa: E402
from django_checkout.models import APIAdminKey, APIKey  # noqa: E402
from django_checkout.views.admin.customer import admin_customer_delete  # noqa: E402
from django_checkout.views.cart import get_or_put_cart  # noqa: E402
from tests.conftest import ERASE_SCOPE, STOREFRONT_SCOPE  # noqa: E402

CHANNEL = "test-channel"
OTHER = "other-channel"
API_KEY, ADMIN_KEY = "HTTP_X_API_KEY", "HTTP_X_API_ADMIN_KEY"
SYSTEM = Actor()
factory = APIRequestFactory()


def _v1(cart, key: str, channel_idx: str = CHANNEL, header: str = API_KEY):
    request = factory.get("/", **{header: key})
    return get_or_put_cart(request, channel_idx=channel_idx, cart_id=str(cart.cart_id), version="1")


def _v2(key: str, channel_idx: str = CHANNEL, header: str = API_KEY):
    return CountriesView.as_view()(factory.get("/countries/", **{header: key}), channel_idx=channel_idx)


def _erase(key: str, channel_idx: str = CHANNEL, header: str = ADMIN_KEY):
    body = json.dumps({"email": "erase@test.com"})
    request = factory.delete("/", data=body, content_type="application/json", **{header: key})
    anonymized = (True, ["order-1"], [], [], [])
    with patch("django_checkout.views.admin.customer.CustomerService.anonymize_customer", return_value=anonymized):
        return admin_customer_delete(request, channel_idx=channel_idx, version="1")


def _outcome(response) -> tuple[int, bytes]:
    if hasattr(response, "render"):
        response.render()
    return response.status_code, response.content


@pytest.fixture
def issue(db):
    """Issue a token of one scope; erase (a secret scope) gets the expiry it must carry."""
    application = Application.objects.create(name="checkout-tests")

    def issue(scope: str = STOREFRONT_SCOPE, channel_idx: str | None = None) -> tuple[ApiToken, str]:
        expiry = timezone.now() + timedelta(days=30) if scope == ERASE_SCOPE else None
        return issue_token(application, scopes=[scope], channel_idx=channel_idx, expires_at=expiry, actor=SYSTEM)

    return issue


@pytest.fixture
def routes(cart):
    """Each keyed route: its scope and a call ``(key, channel_idx) -> response``."""
    return {
        "v1": (STOREFRONT_SCOPE, lambda key, idx=CHANNEL: _v1(cart, key, idx)),
        "v2": (STOREFRONT_SCOPE, lambda key, idx=CHANNEL: _v2(key, idx)),
        "erase": (ERASE_SCOPE, lambda key, idx=CHANNEL: _erase(key, idx)),
    }


def _other(scope: str) -> str:
    return ERASE_SCOPE if scope == STOREFRONT_SCOPE else STOREFRONT_SCOPE


def _later(days: int):
    return patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(days=days))


@pytest.mark.django_db
class TestTokenLifecycle:
    @pytest.mark.parametrize("name", ["v1", "v2", "erase"])
    def test_revoked_token_is_refused(self, name, routes, channel, issue):
        scope, call = routes[name]
        token, raw = issue(scope, CHANNEL)
        assert call(raw).status_code == 200
        revoke_token(token, actor=SYSTEM)
        assert call(raw).status_code in (400, 401)

    def test_legacy_token_without_expiry_keeps_working(self, channel, make_api_key):
        raw = make_api_key(channel=channel)
        token = ApiToken.objects.get(key_hash=hash_key(raw))
        assert (token.legacy, token.expires_at) == (True, None)
        assert _v2(raw).status_code == 200
        with _later(days=3650):
            assert _v2(raw).status_code == 200

    def test_legacy_token_past_its_team_expiry_is_refused(self, channel, make_api_key):
        raw = make_api_key(channel=channel)
        token = ApiToken.objects.get(key_hash=hash_key(raw))
        set_token_expiry(token, expires_at=timezone.now() + timedelta(days=1), actor=SYSTEM)
        assert _v2(raw).status_code == 200
        with _later(days=2):
            assert _v2(raw).status_code == 401

    def test_key_only_in_the_legacy_table_is_refused(self, cart, channel):
        storefront, admin_key = secrets.token_hex(32), secrets.token_hex(32)
        APIKey.objects.create(channel=channel, key=storefront)
        APIAdminKey.objects.create(channel=channel, key=admin_key)
        assert _v1(cart, storefront).status_code == 400
        assert _v2(storefront).status_code == 401
        assert _erase(admin_key).status_code == 401


@pytest.mark.django_db
class TestScopeAndChannel:
    def test_token_of_another_channel_is_refused(self, cart, channel, other_channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE, OTHER)
        _, erase = issue(ERASE_SCOPE, OTHER)
        assert _v1(cart, storefront).status_code == 400
        assert _v2(storefront).status_code == 401
        assert _erase(erase).status_code == 401

    def test_unpinned_token_works_on_any_channel(self, channel, other_channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE)
        _, erase = issue(ERASE_SCOPE)
        for idx in (CHANNEL, OTHER):
            assert _v2(storefront, idx).status_code == 200
            assert _erase(erase, idx).status_code == 200

    def test_erase_token_is_refused_on_storefront_routes(self, cart, channel, issue):
        _, erase = issue(ERASE_SCOPE, CHANNEL)
        assert _v1(cart, erase).status_code == 400
        assert _v2(erase).status_code == 401

    def test_storefront_token_is_refused_on_the_erase_route(self, channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE, CHANNEL)
        assert _erase(storefront).status_code == 401
        assert _erase(storefront, header=API_KEY).status_code == 401

    def test_storefront_routes_read_only_x_api_key(self, cart, channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE, CHANNEL)
        assert _v1(cart, storefront, header=ADMIN_KEY).status_code == 400
        assert _v2(storefront, header=ADMIN_KEY).status_code == 401

    def test_unknown_channel_answers_the_pinned_status(self, cart, channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE)
        _, erase = issue(ERASE_SCOPE)
        assert _v1(cart, storefront, "no-such-channel").status_code == 404
        assert _v2(storefront, "no-such-channel").status_code == 401
        assert _erase(erase, "no-such-channel").status_code == 404


def _failing_keys(issue, scope: str) -> dict[str, str]:
    """The five ways a key fails, each a real token except ``unknown``."""
    expired, expired_raw = issue(scope, CHANNEL)
    ApiToken.objects.filter(pk=expired.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    revoked, revoked_raw = issue(scope, CHANNEL)
    revoke_token(revoked, actor=SYSTEM)
    return {
        "unknown": "ent_api_" + secrets.token_urlsafe(32),
        "expired": expired_raw,
        "revoked": revoked_raw,
        "wrong scope": issue(_other(scope), CHANNEL)[1],
        "wrong channel": issue(scope, OTHER)[1],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["v1", "v2", "erase"])
def test_every_failure_gives_one_response(name, routes, channel, other_channel, issue):
    scope, call = routes[name]
    outcomes = {kind: _outcome(call(raw)) for kind, raw in _failing_keys(issue, scope).items()}
    assert len(set(outcomes.values())) == 1, outcomes
    assert outcomes["unknown"][0] in (400, 401)


@pytest.mark.django_db
def test_cart_read_costs_at_most_one_query_more_than_legacy(cart, channel, make_api_key, django_assert_max_num_queries):
    raw = make_api_key(channel=channel)

    def read():
        request = factory.get("/", HTTP_X_API_KEY=raw)
        return CartDetailView.as_view()(request, channel_idx=CHANNEL, cart_id=str(cart.cart_id))

    with patch("django_checkout.utils.api_keys.access_installed", return_value=False):
        with CaptureQueriesContext(connection) as legacy:
            assert read().status_code == 200
    with django_assert_max_num_queries(len(legacy) + 1):
        assert read().status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("model", [APIKey, APIAdminKey])
def test_admin_pages_never_show_the_raw_key(model, channel, admin_user, rf, settings):
    import django_checkout.admin  # noqa: F401 — registers the key admins (no autodiscover in the test settings)

    settings.ROOT_URLCONF = "tests.admin_urls"  # the module has no URLconf of its own
    key = model.objects.create(channel=channel)
    model_admin = admin.site.get_model_admin(model)
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, key)
    for response in (model_admin.changelist_view(request), model_admin.change_view(request, str(key.pk))):
        html = response.render().content.decode()
        assert response.status_code == 200
        assert key.key not in html
        assert f"…{key.key[-4:]}" in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("command", "model", "scope"),
    [("generate-api-key", APIKey, STOREFRONT_SCOPE), ("generate-api-admin-key", APIAdminKey, ERASE_SCOPE)],
)
def test_key_commands_refuse_and_name_the_token_command(command, model, scope, channel):
    # By module: accounts' generate-api-admin-key shadows checkout's under the same name.
    module = import_module(f"django_checkout.management.commands.{command}")
    with pytest.raises(CommandError, match=f"access_token create --scope {scope} --channel {CHANNEL}"):
        call_command(module.Command(), CHANNEL)
    assert not model.objects.exists()
