# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Two exploitable defects found in the 8.2.0 review.

1. ``payment_redirect_bridge`` merged query params as
   ``{**status_qs, **incoming_qs, **target_qs}``. Later keys win, so the caller's own
   query string beat the status this server computed: opening your own bridge link with
   ``?payment_status=COMPLETE`` handed the mobile app a forged success.

2. The voucher brute-force throttle keyed on ``X-Forwarded-For`` (DRF's ``get_ident``
   falls back to it whenever ``NUM_PROXIES`` is unset, which is the default), so rotating
   one header reset the counter. The v1 cart path — the one the storefront actually uses —
   had no throttle at all.
"""

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.api.v2.views.cart_views import _CartPaymentThrottle
from django_checkout.domain.payment_redirect_target import is_allowed_bridge_target
from django_checkout.utils.api.throttling import TooManyRequests, client_ip, enforce_ip_rate_limit, throttle_ident
from django_checkout.views.cart import _carries_pay_code, _throttle_pay_code

factory = RequestFactory()

VOUCHER_CODE = "26WT1234"


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


# --- Throttle identity ------------------------------------------------------------------


def test_client_ip_ignores_forwarded_for():
    """The whole bypass: a client-supplied header must not key the counter."""
    request = factory.get("/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="10.0.0.1")

    assert client_ip(request) == "10.0.0.1"


def test_rotating_forwarded_for_does_not_reset_the_counter():
    requests = [factory.get("/", HTTP_X_FORWARDED_FOR=f"9.9.9.{n}", REMOTE_ADDR="10.0.0.1") for n in range(4)]

    for request in requests[:3]:
        enforce_ip_rate_limit(request, scope="test", limit=3, window_seconds=60)

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(requests[3], scope="test", limit=3, window_seconds=60)


def test_separate_ips_have_separate_budgets():
    first = factory.get("/", REMOTE_ADDR="10.0.0.1")
    second = factory.get("/", REMOTE_ADDR="10.0.0.2")

    enforce_ip_rate_limit(first, scope="test", limit=1, window_seconds=60)
    enforce_ip_rate_limit(second, scope="test", limit=1, window_seconds=60)  # must not raise

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(first, scope="test", limit=1, window_seconds=60)


# --- Throttle identity behind a reverse proxy --------------------------------------------
# REMOTE_ADDR is the proxy there: one address, so one bucket, for the whole shop.

PROXY = "10.0.0.1"


@pytest.fixture
def num_proxies(settings):
    """Pin DRF's NUM_PROXIES; assigning REST_FRAMEWORK reloads ``api_settings``."""

    def _set(count: int) -> None:
        settings.REST_FRAMEWORK = {**settings.REST_FRAMEWORK, "NUM_PROXIES": count}

    return _set


def _behind_proxy(forwarded_for: str):
    return factory.get("/", HTTP_X_FORWARDED_FOR=forwarded_for, REMOTE_ADDR=PROXY)


def test_two_clients_behind_one_proxy_have_separate_budgets(num_proxies):
    num_proxies(1)
    first, second = _behind_proxy("203.0.113.7"), _behind_proxy("203.0.113.8")

    enforce_ip_rate_limit(first, scope="test", limit=1, window_seconds=60)
    enforce_ip_rate_limit(second, scope="test", limit=1, window_seconds=60)  # must not raise

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(first, scope="test", limit=1, window_seconds=60)


def test_only_the_address_the_proxy_appended_counts(num_proxies):
    """The client writes the left of X-Forwarded-For; the proxy appends the right."""
    num_proxies(1)

    enforce_ip_rate_limit(_behind_proxy("1.1.1.1, 203.0.113.7"), scope="test", limit=1, window_seconds=60)

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(_behind_proxy("2.2.2.2, 203.0.113.7"), scope="test", limit=1, window_seconds=60)


def test_forwarded_for_is_ignored_without_a_proxy(num_proxies):
    num_proxies(0)

    enforce_ip_rate_limit(_behind_proxy("203.0.113.7"), scope="test", limit=1, window_seconds=60)

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(_behind_proxy("203.0.113.8"), scope="test", limit=1, window_seconds=60)


CART_A = "0b5f0f0e-3c1a-4d2b-9a7e-5c1d2e3f4a5b"
CART_B = "7d9c2a1b-8e4f-4c3d-b2a1-6f5e4d3c2b1a"


def test_one_cart_cannot_drain_another_carts_allowance():
    request = factory.get("/", REMOTE_ADDR=PROXY)

    enforce_ip_rate_limit(request, scope="test", limit=1, window_seconds=60, cart_id=CART_A)
    enforce_ip_rate_limit(request, scope="test", limit=1, window_seconds=60, cart_id=CART_B)  # must not raise

    with pytest.raises(TooManyRequests):
        enforce_ip_rate_limit(request, scope="test", limit=1, window_seconds=60, cart_id=CART_A)


@pytest.mark.parametrize("spelling", [CART_A.upper(), CART_A.replace("-", ""), f"{{{CART_A}}}", uuid.UUID(CART_A)])
def test_another_spelling_of_a_cart_id_is_the_same_bucket(spelling):
    request = factory.get("/", REMOTE_ADDR=PROXY)

    assert throttle_ident(request, spelling) == throttle_ident(request, CART_A)


@pytest.mark.parametrize("not_a_cart", [None, "", "not-a-uuid", "a b\n" * 100])
def test_what_is_not_a_cart_id_never_reaches_the_key(not_a_cart):
    request = factory.get("/", REMOTE_ADDR=PROXY)

    assert throttle_ident(request, not_a_cart) == PROXY


def test_v1_pay_code_throttle_is_per_cart(monkeypatch):
    monkeypatch.setattr(checkout_settings, "PAY_CODE_RATE_LIMIT", 1)
    request, voucher = factory.get("/", REMOTE_ADDR=PROXY), [{"code": "voucher", "pay_code": VOUCHER_CODE}]

    _throttle_pay_code(request, voucher, CART_A)
    _throttle_pay_code(request, voucher, CART_B)  # must not raise

    with pytest.raises(TooManyRequests):
        _throttle_pay_code(request, voucher, CART_A)


class _CartView:
    def __init__(self, cart_id: str) -> None:
        self.kwargs = {"cart_id": cart_id}


def test_v2_payment_throttle_keys_on_the_forwarded_client(num_proxies):
    num_proxies(1)
    throttle, view = _CartPaymentThrottle(), _CartView(CART_A)

    first = throttle.get_cache_key(_behind_proxy("203.0.113.7"), view)
    second = throttle.get_cache_key(_behind_proxy("203.0.113.8"), view)

    assert first != second


def test_v2_payment_throttle_keys_on_the_cart():
    throttle, request = _CartPaymentThrottle(), factory.get("/", REMOTE_ADDR=PROXY)

    assert throttle.get_cache_key(request, _CartView(CART_A)) != throttle.get_cache_key(request, _CartView(CART_B))


def test_scopes_do_not_share_a_counter():
    request = factory.get("/", REMOTE_ADDR="10.0.0.1")

    enforce_ip_rate_limit(request, scope="a", limit=1, window_seconds=60)
    enforce_ip_rate_limit(request, scope="b", limit=1, window_seconds=60)  # must not raise


def test_too_many_requests_is_429():
    assert TooManyRequests.status_code == 429


# --- Which v1 requests get throttled ----------------------------------------------------
# Ordinary cart editing must stay unthrottled; only the guessable code is the vector.


@pytest.mark.parametrize(
    "payment_method,expected",
    [
        (None, False),
        ([], False),
        ([{"code": "banktransfer"}], False),
        ([{"code": "voucher", "pay_code": VOUCHER_CODE}], True),
        ([{"code": "banktransfer"}, {"code": "voucher", "pay_code": VOUCHER_CODE}], True),
        ({"code": "voucher", "pay_code": VOUCHER_CODE}, True),  # legacy single dict
        ([{"code": "voucher", "pay_code": None}], False),
        ([{"code": "voucher", "pay_code": ""}], False),
    ],
)
def test_carries_pay_code(payment_method, expected):
    assert _carries_pay_code(payment_method) is expected


def test_carries_pay_code_reads_dto_objects():
    """v1 hands in a parsed marshmallow DTO, not a dict."""

    class _Entry:
        code = "voucher"
        pay_code = VOUCHER_CODE

    assert _carries_pay_code([_Entry()]) is True


# --- Redirect bridge precedence ---------------------------------------------------------


def _merge(status_qs, incoming_qs, target_qs):
    """The merge as the view performs it — server status must win."""
    return {**incoming_qs, **target_qs, **status_qs}


def test_caller_cannot_forge_payment_status():
    merged = _merge(
        status_qs={"order_id": "0000000042", "order_status": "unpaid", "payment_status": "pending"},
        incoming_qs={"payment_status": "COMPLETE", "order_status": "confirmed", "order_id": "ATTACKER"},
        target_qs={},
    )

    assert merged["payment_status"] == "pending"
    assert merged["order_status"] == "unpaid"
    assert merged["order_id"] == "0000000042"


def test_merchant_target_params_still_beat_the_gateway():
    merged = _merge(
        status_qs={"order_status": "unpaid"},
        incoming_qs={"utm": "gateway", "theme": "hijacked"},
        target_qs={"theme": "merchant"},
    )

    assert merged["theme"] == "merchant"
    assert merged["utm"] == "gateway"  # unrelated gateway params still forwarded


def test_status_wins_even_when_target_url_carries_the_same_key():
    """A stale order_id baked into the merchant URL must not shadow the live one."""
    merged = _merge(status_qs={"order_id": "0000000042"}, incoming_qs={}, target_qs={"order_id": "<order_id>"})

    assert merged["order_id"] == "0000000042"


# --- Throttle rate is configurable ------------------------------------------------------
# A class-level `rate` makes DRF skip get_rate() entirely, hard-wiring the limit.


@pytest.fixture
def throttle_rates(monkeypatch):
    """Set DEFAULT_THROTTLE_RATES somewhere the throttle actually reads.

    ``SimpleRateThrottle.THROTTLE_RATES`` is a class attribute bound at class-creation
    time to the dict ``api_settings`` held then. Reassigning ``settings.REST_FRAMEWORK``
    builds a *new* dict, which an already-imported throttle class never sees — so the
    override silently did nothing and every rate assertion below collapsed onto the
    fallback, passing whether or not the override worked.
    """

    def _set(rates: dict) -> None:
        monkeypatch.setattr(_CartPaymentThrottle, "THROTTLE_RATES", rates)

    return _set


def test_rate_falls_back_when_scope_is_not_configured(throttle_rates):
    throttle_rates({})

    assert _CartPaymentThrottle().get_rate() == _CartPaymentThrottle._FALLBACK_RATE


def test_rate_honours_deployment_override(throttle_rates):
    throttle_rates({"checkout_cart_payment": "5/minute"})

    assert _CartPaymentThrottle().get_rate() == "5/minute"


def test_malformed_rate_falls_back_instead_of_disabling_throttling(throttle_rates):
    """In DRF a None/garbage rate means "no throttling" — the invariant is it never happens."""
    throttle_rates({"checkout_cart_payment": "nonsense"})

    assert _CartPaymentThrottle().get_rate() == _CartPaymentThrottle._FALLBACK_RATE


# --- Bridge target allowlist ------------------------------------------------------------
# The scheme alone never bounded the destination; target_url is client-supplied.


@pytest.fixture
def bridge_config(monkeypatch):
    """django_checkout.settings binds its constants with getattr() at import time, so the
    pytest-django ``settings`` fixture cannot reach them — patch the module instead."""

    def configure(schemes, hosts):
        monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES", schemes)
        monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_ALLOWED_HOSTS", hosts)

    return configure


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://shop.example/thanks", True),
        ("https://evil.example/phish", False),
        ("https://shop.example.evil.example/", False),  # suffix must not match by accident
        ("https://SHOP.EXAMPLE/thanks", True),  # hostname is lowercased by urlparse
        ("https://shop.example:443/thanks", True),  # port is not part of hostname
        ("http://shop.example/thanks", False),  # http not in TARGET_SCHEMES
    ],
)
def test_web_targets_are_checked_against_the_host_allowlist(bridge_config, url, expected):
    bridge_config(["https"], ["shop.example"])

    assert is_allowed_bridge_target(urlparse(url)) is expected


def test_userinfo_cannot_disguise_the_real_host(bridge_config):
    """urlparse(...).netloc still contains shop.example, but the browser goes to evil.example."""
    bridge_config(["https"], ["shop.example"])

    assert is_allowed_bridge_target(urlparse("https://shop.example@evil.example/")) is False


def test_native_app_scheme_needs_no_host(bridge_config):
    """exp://192.168.1.5:19000 varies per developer machine — the scheme is the control."""
    bridge_config(["exp"], [])

    assert is_allowed_bridge_target(urlparse("exp://192.168.1.5:19000/--/checkout")) is True


def test_scheme_outside_the_allowlist_is_still_rejected(bridge_config):
    bridge_config(["exp"], ["shop.example"])

    assert is_allowed_bridge_target(urlparse("myapp://back")) is False


def test_empty_host_allowlist_denies_every_web_target(bridge_config):
    """Fail closed: an unconfigured deployment must not get an open redirector."""
    bridge_config(["https"], [])

    assert is_allowed_bridge_target(urlparse("https://shop.example/thanks")) is False


def test_merge_round_trips_through_a_url():
    merged = _merge({"payment_status": "pending"}, {"payment_status": "COMPLETE"}, {})
    query = parse_qs(urlparse(f"myapp://back?{'&'.join(f'{k}={v}' for k, v in merged.items())}").query)

    assert query["payment_status"] == ["pending"]
