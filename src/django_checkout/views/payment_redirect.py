# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.http import HttpResponse, HttpResponseNotFound
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django_checkout.domain.payment_redirect_target import is_allowed_bridge_target
from django_checkout.models import PaymentRedirect


@csrf_exempt
@require_http_methods(["GET"])
def payment_redirect_bridge(request, version, channel_idx, token):
    """Bridge endpoint for payment providers that reject custom URL schemes
    (e.g. exp:// for Expo). The gateway redirects here and we 302 to the original
    target_url stored in PaymentRedirect.

    Security:
    - The token (UUID4) is opaque and unguessable.
    - Single-use + TTL atomically: one conditional UPDATE wins the race between
      parallel requests; replay → 404.
    - Defensive double-check of the target — scheme AND host for http/https (in case
      the allowlist was narrowed after the row was created). Without the host check
      this would be an open redirector on the shop's domain and certificate.
    - Query param precedence, lowest first: incoming gateway params, then the
      merchant's target_url params, finally the server-computed status. This way
      ?order_id=ATTACKER or ?payment_status=COMPLETE from the caller cannot override
      the values the mobile app relies on.
    - No HTML in the response (Location header only) → no XSS surface.
    - Uniform 404 message for every failure (does not reveal token state).
    """
    now = timezone.now()
    claimed = PaymentRedirect.objects.filter(token=token, consumed_at__isnull=True, expires_at__gt=now).update(
        consumed_at=now
    )
    if not claimed:
        return HttpResponseNotFound("Redirect link invalid")

    redirect_obj = PaymentRedirect.objects.get(token=token)
    parsed = urlparse(redirect_obj.target_url)
    if not is_allowed_bridge_target(parsed):
        return HttpResponseNotFound("Redirect link invalid")

    # Order & payment status for mobile app
    order = redirect_obj.order
    payment_intent = order.payment_items.first()
    status_qs = {
        "order_id": order.pretty_id,
        "order_status": order.order_status,
        "payment_status": payment_intent.payment_status if payment_intent else "unknown",
    }

    target_qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    incoming_qs = dict(request.GET.items())
    # Precedence, lowest to highest: gateway params, then the merchant's own target_url
    # params, then the status this server computed. status_qs MUST come last — it is the
    # only part the mobile app is meant to trust, and the caller controls incoming_qs.
    merged_qs = {**incoming_qs, **target_qs, **status_qs}
    final_url = urlunparse(parsed._replace(query=urlencode(merged_qs)))

    # Raw HttpResponse 302 — Django's HttpResponseRedirect enforces allowed_schemes
    # ["http", "https", "ftp"] and rejects custom schemes (exp://, myapp://).
    response = HttpResponse(status=302)
    response["Location"] = final_url
    return response
