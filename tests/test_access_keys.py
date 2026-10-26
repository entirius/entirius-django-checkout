# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The access path: with django_access installed every checkout key is an access token (``verify_api_key``).

Legacy keys reach it only through the import (``make_api_key``); the legacy tables are never read on this path.
"""

import json
import os
import secrets
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

import pytest

pytest.importorskip("django_access")
if os.environ.get("ENTIRIUS_TEST_NO_ACCESS"):  # make test-legacy: django_access is importable but not installed
    pytest.skip("access path only", allow_module_level=True)

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
REFUSED = {"v1": 400, "v2": 401, "erase": 401}  # the status every refusal gets, per route
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


def _cart_of(channel):
    from django_checkout.services import cart_service

    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    return record


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
        revoked = _outcome(call(raw))
        assert revoked[0] == REFUSED[name]
        assert revoked == _outcome(call(secrets.token_hex(32)))
        assert revoked == _outcome(call(""))

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
        for record in (channel, other_channel):
            idx, cart = record.idx, _cart_of(record)
            assert _v1(cart, storefront, idx).status_code == 200
            assert _v2(storefront, idx).status_code == 200
            assert _erase(erase, idx).status_code == 200

    def test_erase_token_is_refused_on_storefront_routes(self, cart, channel, issue):
        _, erase = issue(ERASE_SCOPE, CHANNEL)
        assert _v1(cart, erase).status_code == 400
        assert _v2(erase).status_code == 401

    def test_storefront_token_is_refused_on_the_erase_route(self, channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE, CHANNEL)
        _, erase = issue(ERASE_SCOPE, CHANNEL)
        assert _erase(storefront).status_code == 401
        assert _erase(storefront, header=API_KEY).status_code == 401
        assert _erase(erase, header=API_KEY).status_code == 401

    def test_storefront_routes_read_only_x_api_key(self, cart, channel, issue):
        _, storefront = issue(STOREFRONT_SCOPE, CHANNEL)
        assert _v1(cart, storefront, header=ADMIN_KEY).status_code == 400
        assert _v2(storefront, header=ADMIN_KEY).status_code == 401

    def test_unknown_channel_answers_the_pinned_status(self, cart, channel, issue):
        storefront_token, storefront = issue(STOREFRONT_SCOPE)
        erase_token, erase = issue(ERASE_SCOPE)
        assert _v1(cart, storefront, "no-such-channel").status_code == 404
        assert _v2(storefront, "no-such-channel").status_code == 401
        assert _erase(erase, "no-such-channel").status_code == 404
        for token in (storefront_token, erase_token):
            token.refresh_from_db()
            assert token.last_used_at is None


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
    assert outcomes["unknown"][0] == REFUSED[name]
    assert outcomes["unknown"] == _outcome(call(""))


@pytest.mark.django_db
def test_cart_read_costs_at_most_one_query_more_than_legacy(cart, channel, make_api_key, django_assert_max_num_queries):
    raw = make_api_key(channel=channel)

    def read():
        request = factory.get("/", HTTP_X_API_KEY=raw)
        return CartDetailView.as_view()(request, channel_idx=CHANNEL, cart_id=str(cart.cart_id))

    with patch("django_checkout.utils.api_keys.access_installed", return_value=False):
        with CaptureQueriesContext(connection) as legacy:
            assert read().status_code == 200
    with CaptureQueriesContext(connection) as first:
        assert read().status_code == 200
    assert len(first) <= len(legacy) + 1
    with django_assert_max_num_queries(len(first)):  # a repeat read costs no more than the first
        assert read().status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("model", [APIKey, APIAdminKey])
def test_admin_pages_never_show_the_raw_key(model, channel, admin_user, rf):
    import django_checkout.admin  # noqa: F401 — registers the key admins (no autodiscover in the test settings)

    key = model.objects.create(channel=channel)
    model_admin = admin.site.get_model_admin(model)
    request = rf.get("/")
    request.user = admin_user
    assert not model_admin.has_add_permission(request)
    assert not model_admin.has_change_permission(request, key)
    assert not model_admin.has_delete_permission(request, key)
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


# A pinned token erases only in its channel (D3), by customer e-mail and by body match; an unpinned one everywhere.

ERASED = "erased@test.com"


def _body(email: str) -> dict:
    return {"addresses": {"billing_address": {"firstname": "Jan", "lastname": "Kowalski", "email": email}}}


def _rows(channel, label: str) -> list:
    """An order and a cart found by their customer's e-mail, and an order and a cart found by their body."""
    from django.contrib.auth import get_user_model
    from django_accounts.models import Customer

    from django_checkout.models import Cart, Order

    user = get_user_model().objects.create_user(username=f"{label}-{ERASED}", email=ERASED)
    customer = Customer.objects.create(user=user)
    by_customer, by_body = {"customer": customer, "channel": channel}, {"channel": channel}
    return [
        Order.objects.create(order_body=_body("someone@test.com"), **by_customer),
        Cart.objects.create(cart_body=_body("someone@test.com"), **by_customer),
        Order.objects.create(order_body=_body(ERASED), **by_body),
        Cart.objects.create(cart_body=_body(ERASED), **by_body),
    ]


def _anonymized(rows: list) -> list[bool]:
    for row in rows:
        row.refresh_from_db()
    bodies = [getattr(row, "order_body", None) or row.cart_body for row in rows]
    return [body["addresses"]["billing_address"]["firstname"] != "Jan" for body in bodies]


def _erase_email(key: str, email: str = ERASED):
    body = json.dumps({"email": email})
    request = factory.delete("/", data=body, content_type="application/json", **{ADMIN_KEY: key})
    return admin_customer_delete(request, channel_idx=CHANNEL, version="1")


@pytest.mark.django_db
class TestPinnedErase:
    def test_pinned_token_erases_only_its_channel(self, channel, other_channel, issue):
        own, other = _rows(channel, "a"), _rows(other_channel, "b")
        response = _erase_email(issue(ERASE_SCOPE, CHANNEL)[1])
        assert response.status_code == 200
        data = json.loads(response.content)["data"]
        assert sorted(data["orders"]) == sorted(str(own[i].order_id) for i in (0, 2))
        assert sorted(data["carts"]) == sorted(str(own[i].cart_id) for i in (1, 3))
        assert _anonymized(own) == [True] * 4
        assert _anonymized(other) == [False] * 4

    def test_email_only_in_another_channel_is_not_found(self, channel, other_channel, issue):
        other = _rows(other_channel, "b")
        raw = issue(ERASE_SCOPE, CHANNEL)[1]
        response = _erase_email(raw)
        assert response.status_code == 404
        assert _outcome(response) == _outcome(_erase_email(raw, "nobody@test.com"))
        assert _anonymized(other) == [False] * 4

    @pytest.mark.parametrize(("pinned", "expected"), [(True, CHANNEL), (False, None)])
    def test_signal_carries_the_erase_channel(self, pinned, expected, channel, issue):
        from django_checkout.signals import customer_anonymized_signal

        received = []

        def receiver(sender, **kwargs):
            received.append(kwargs["channel_idx"])

        customer_anonymized_signal.connect(receiver)
        try:
            _erase_email(issue(ERASE_SCOPE, CHANNEL if pinned else None)[1])
        finally:
            customer_anonymized_signal.disconnect(receiver)
        assert received == [expected]

    def test_unpinned_token_erases_every_channel(self, channel, other_channel, issue):
        own, other = _rows(channel, "a"), _rows(other_channel, "b")
        assert _erase_email(issue(ERASE_SCOPE)[1]).status_code == 200
        assert _anonymized(own + other) == [True] * 8
