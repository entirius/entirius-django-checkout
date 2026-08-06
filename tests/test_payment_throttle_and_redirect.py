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

from urllib.parse import parse_qs, urlparse

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from django_checkout import settings as checkout_settings
from django_checkout.api.v2.views.cart_views import _CartPaymentThrottle
from django_checkout.domain.payment_redirect_target import is_allowed_bridge_target
from django_checkout.utils.api.throttling import TooManyRequests, client_ip, enforce_ip_rate_limit
from django_checkout.views.cart import _carries_pay_code

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
