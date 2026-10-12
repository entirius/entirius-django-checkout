# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Client rate limiting shared by the v1 function views and the v2 DRF throttles.

DRF's throttling only reaches APIView subclasses. The v1 checkout views are plain Django
functions and accept the same voucher ``pay_code``, so throttling only v2 left the
brute-force vector fully open on the path the storefront actually uses.
"""

import uuid

from django.core.cache import cache
from django_utils.api.exceptions import APIException
from rest_framework.settings import api_settings
from rest_framework.throttling import BaseThrottle

CACHE_KEY = "checkout:throttle:{scope}:{ident}"


class TooManyRequests(APIException):
    status = "TOO_MANY_REQUESTS"
    message = "Too many requests."
    status_code = 429


def client_ip(request) -> str:
    """The client address: DRF's ``get_ident`` once the proxy depth is pinned, the TCP peer otherwise.

    Behind a reverse proxy REMOTE_ADDR is the proxy, so every client would share one bucket.
    With ``NUM_PROXIES`` set, ``get_ident`` reads only the ``X-Forwarded-For`` entries the
    trusted proxies appended (none when it is 0). Unset, it would trust the whole header,
    which the client writes: rotate it, reset the counter. Then only REMOTE_ADDR counts.
    """
    if api_settings.NUM_PROXIES is None:
        return request.META.get("REMOTE_ADDR") or "unknown"
    return BaseThrottle().get_ident(request) or "unknown"


def throttle_ident(request, cart_id: object = None) -> str:
    """The client, narrowed to one cart when the request names it: carts never share an allowance.

    The id is keyed in its canonical form: a UUID has many spellings (case, hyphens, braces)
    that reach the same cart, and each would otherwise open a fresh allowance. Anything that
    is not a UUID names no cart, so it gets the client's own bucket.
    """
    try:
        return f"{client_ip(request)}:{uuid.UUID(str(cart_id))}"
    except ValueError:
        return client_ip(request)


def enforce_ip_rate_limit(request, *, scope: str, limit: int, window_seconds: int, cart_id: object = None) -> None:
    """Raise TooManyRequests once this client (and cart) exceeds ``limit`` hits in the window.

    ``cache.incr`` does not extend the TTL, so the window is fixed from the first hit
    rather than sliding forward with every request.
    """
    key = CACHE_KEY.format(scope=scope, ident=throttle_ident(request, cart_id))
    try:
        count = cache.incr(key)
    except ValueError:  # first hit in this window — incr needs an existing key
        cache.set(key, 1, window_seconds)
        count = 1

    if count > limit:
        raise TooManyRequests(message="Too many attempts. Try again later.")
