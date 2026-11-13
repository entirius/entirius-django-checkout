# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Per-user throttles of admin endpoints that send mail.

``DEFAULT_THROTTLE_RATES[<scope>]`` overrides a rate; the class ``fallback_rate`` is the safety net when the scope
is unset or malformed. The fallback must not live in a class ``rate``: DRF's ``SimpleRateThrottle.__init__`` calls
``get_rate()`` only when ``rate`` is unset, so a class ``rate`` would silently disable the settings override.
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import UserRateThrottle


class _FallbackRateMixin:
    fallback_rate: str

    def get_rate(self) -> str:
        """Never None or a malformed rate: in DRF None means no throttling, a malformed one a 500."""
        try:
            rate = super().get_rate()
            self.parse_rate(rate)
        except (ImproperlyConfigured, ValueError, KeyError, TypeError):  # unset or malformed
            return self.fallback_rate
        return rate if rate else self.fallback_rate


class OrderMailResendThrottle(_FallbackRateMixin, UserRateThrottle):
    scope = "checkout_order_mail_resend"
    fallback_rate = "30/hour"
