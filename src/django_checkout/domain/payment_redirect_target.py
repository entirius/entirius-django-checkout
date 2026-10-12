# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Which continue_url targets the payment bridge may redirect to.

``target_url`` comes from the client (``payment_method.continue_url``), so the bridge is a
redirector whose destination an attacker picks. Checking only the URL scheme leaves it
open: the resulting link sits on the shop's own domain and TLS certificate, which is what
makes it worth phishing with.

Shared by ``base_payment_provider.resolve_continue_url`` (before a PaymentRedirect row is
created) and by the bridge view (defensive re-check, in case the allowlist was tightened
after the row was written). Keeping one implementation means the two cannot drift into
disagreeing about what is allowed.
"""

from urllib.parse import ParseResult

from django_checkout import settings

# Schemes a browser will follow to an arbitrary host. Everything else is a native app
# scheme (exp://, myapp://) where there is no meaningful host to compare and the scheme
# allowlist is the whole control.
WEB_SCHEMES = frozenset({"http", "https"})


def is_allowed_bridge_target(parsed: ParseResult) -> bool:
    """True when the bridge may 302 to this parsed target URL."""
    if parsed.scheme not in settings.PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES:
        return False
    if parsed.scheme not in WEB_SCHEMES:
        return True
    # hostname, not netloc: urlparse("https://shop.example@evil.com/").netloc still
    # contains "shop.example", but the browser navigates to evil.com. hostname is the
    # destination that actually matters, already lowercased and stripped of the port.
    return parsed.hostname in set(settings.PAYMENT_REDIRECT_ALLOWED_HOSTS)
