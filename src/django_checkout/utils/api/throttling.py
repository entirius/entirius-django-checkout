# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""IP rate limiting shared by the v1 function views and the v2 DRF throttles.

DRF's throttling only reaches APIView subclasses. The v1 checkout views are plain Django
functions and accept the same voucher ``pay_code``, so throttling only v2 left the
brute-force vector fully open on the path the storefront actually uses.
"""

from django.core.cache import cache
from django_utils.api.exceptions import APIException

CACHE_KEY = "checkout:throttle:{scope}:{ident}"


class TooManyRequests(APIException):
    status = "TOO_MANY_REQUESTS"
    message = "Too many requests."
    status_code = 429


def client_ip(request) -> str:
    """The TCP peer address — never a client-supplied header.

    ``X-Forwarded-For`` is attacker-controlled unless the proxy depth is pinned, so a
    limiter keyed on it is a limiter in name only: rotate the header, reset the counter.
    REMOTE_ADDR is set by the WSGI server from the actual connection.
    """
    return request.META.get("REMOTE_ADDR") or "unknown"


def enforce_ip_rate_limit(request, *, scope: str, limit: int, window_seconds: int) -> None:
    """Raise TooManyRequests once this client IP exceeds ``limit`` hits in the window.

    ``cache.incr`` does not extend the TTL, so the window is fixed from the first hit
    rather than sliding forward with every request.
    """
    key = CACHE_KEY.format(scope=scope, ident=client_ip(request))
    try:
        count = cache.incr(key)
    except ValueError:  # first hit in this window — incr needs an existing key
        cache.set(key, 1, window_seconds)
        count = 1

    if count > limit:
        raise TooManyRequests(message="Too many attempts. Try again later.")
