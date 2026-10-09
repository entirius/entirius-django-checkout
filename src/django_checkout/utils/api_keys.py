# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""The one key check of the storefront (X-API-KEY) and erase (X-API-ADMIN-KEY) routes.

With ``django_access`` installed a key is an access token checked by ``verify_api_key`` (scope + channel pin); the
legacy tables are never read on that path — legacy keys live on as imported tokens (D28). Without the module:
today's legacy query, unchanged.
"""

from types import SimpleNamespace

from django.apps import apps

from django_checkout.models import APIAdminKey, APIKey

STOREFRONT_SCOPE = "checkout.storefront"
ERASE_SCOPE = "checkout.erase"
_HEADERS = {STOREFRONT_SCOPE: "HTTP_X_API_KEY", ERASE_SCOPE: "HTTP_X_API_ADMIN_KEY"}
_LEGACY_MODELS = {STOREFRONT_SCOPE: APIKey, ERASE_SCOPE: APIAdminKey}


def access_installed() -> bool:
    return apps.is_installed("django_access")


def mask_key(key: str) -> str:
    """What an admin page shows of a key: its last four characters."""
    return f"…{key[-4:]}"


def key_is_valid(request, *, scope: str, channel_idx: str | None) -> bool:
    """True when the route's own header carries a key for ``scope`` on ``channel_idx``."""
    key = request.META.get(_HEADERS[scope])
    if not key:
        return False
    if access_installed():
        return _token_is_valid(request, key, scope, channel_idx)
    return _LEGACY_MODELS[scope].objects.filter(key=key, channel__idx=channel_idx).exists()


def _token_is_valid(request, key: str, scope: str, channel_idx: str | None) -> bool:
    """``verify_api_key`` sees only this route's header: the other key header never stands in for it."""
    from django_access.services.tokens import verify_api_key

    token = verify_api_key(SimpleNamespace(META={_HEADERS[scope]: key}), scope, channel_idx)
    if token is not None:
        request.access_token = token
    return token is not None


def token_command(scope: str, channel_idx: str) -> str:
    """What replaces the legacy key commands when access is installed."""
    command = f"manage.py access_token create --scope {scope} --channel {channel_idx} --application <name>"
    return f"{command} --expires-days <n>" if scope == ERASE_SCOPE else command
