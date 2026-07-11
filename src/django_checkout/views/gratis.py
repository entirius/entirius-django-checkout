# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import authenticate, require_http_method
from django_utils.api.exceptions import NotFound
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.models import Cart
from django_checkout.models.abstract_product_filter import PriceRestrictionType
from django_checkout.utils.api.decorators import channel_view
from django_checkout.utils.api.utils import resolve_country, resolve_currency
from django_checkout.worker.discount_worker import get_all_available_gratis_rules

logger_process = ProcessLogger("CHECKOUT_CART")


def _build_gratis_response(record, customer=None) -> list:
    """Build the list of available gratis (free product) rules for a cart.

    Pure, request-independent: uses ``record.channel`` and an explicit
    ``customer`` so both the v1 function view and the v2 DRF view can share it.
    """
    cart_body = record.as_data
    email = None

    country, currency = resolve_country(record.channel, {"checkout_country": cart_body.country_code}, customer)
    if not currency:
        currency = resolve_currency(record.channel, {"checkout_currency": cart_body.currency_code})

    if (
        record
        and hasattr(record, "addresses")
        and hasattr(record, "addresses.shipping_address")
        and hasattr(record, "addresses.shipping_address.email")
    ):
        email = cart_body.cart.addresses.shipping_address.email

    total_price_discount_from_netto_brutto = (
        cart_body.cart.total_price
        if record.channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
        else cart_body.cart.total_netto_price
    )

    available_gratis_rules, *_ = get_all_available_gratis_rules(
        cart_body,
        total_price_discount_from_netto_brutto,
        record.channel,
        customer,
        email,
        country,
        currency,
    )
    return available_gratis_rules


@csrf_exempt
@channel_view
@authenticate
@require_http_method("GET")
def get_gratis_for_cart(request, cart_id, *args, **kwargs):
    record = Cart.objects.get_record(request, cart_id)

    if record is None:
        logger_process.info("There is no cart with this customer.", extra={})
        raise NotFound

    customer = request.user.customer if (request.user is not None and request.user.is_customer) else None
    return Response(_build_gratis_response(record, customer=customer))
