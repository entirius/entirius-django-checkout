# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django.db.models import F
from django_utils.api.decorators import authenticate, require_http_method
from django_utils.api.exceptions import NotFound
from django_utils.api.responses import PaginatedResponse
from django_utils.api.utils import make_media_url
from process_logger import ProcessLogger

from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.models import Cart
from django_checkout.settings import CUSTOMS_THRESHOLD_ENABLED
from django_checkout.utils.api.decorators import channel_view
from django_checkout.utils.api.utils import paginate, resolve_language

logger_process = ProcessLogger("CHECKOUT_CART")


def _build_shipping_methods_response(record, lang_override: str | None = None) -> list[dict]:
    """Build the list of available shipping methods for a cart.

    Pure, request-independent: sources language from the cart's stored
    ``language_code`` (falling back to the channel default) so both the v1
    function view and the v2 DRF view can share it. Returns the full,
    unpaginated list of method dicts with images already resolved; callers
    paginate/wrap as needed.
    """
    checkout_data = CheckoutData.Schema().load(record.cart_body)
    lang = resolve_language(record.channel, {"lang": lang_override, "checkout_lang": checkout_data.language_code})
    available_methods = record.available_shipping_methods
    base_data = (
        available_methods.order_by("pk")
        .values(
            "pk",
            "country_code",
            "tax_rate",
            "cash_on_delivery_available",
            "cash_on_delivery_fee",
            "matrix_price_brutto",
            "new_price",
            "price_brutto",
            "matrix_price_brutto_modifier",
            position=F("method__position"),
            dpm_code=F("method__dpm_code"),
            code=F("method__code"),
            currency_code=F("currency__iso3"),
            method_type=F("method__method_type"),
            name=F(f"method__name_t9n__{lang}"),
            description=F(f"method__description_t9n__{lang}"),
            image=F("method__image"),
        )
        .order_by("position")
    )
    options_by_pk = {so.pk: so for so in record.available_shipping_methods.select_related("method").order_by("pk")}
    amount_missing_above_modifier = record.amount_missing_for_free_shipping_above_modifier

    # Determine customs threshold scenario from stored cart data
    apply_threshold_to_shipping = False
    if CUSTOMS_THRESHOLD_ENABLED:
        shipping_country = getattr(
            getattr(checkout_data, "addresses", None) and getattr(checkout_data.addresses, "shipping_address", None),
            "country_code",
            None,
        )
        threshold_scenario = getattr(getattr(checkout_data, "cart", None), "customs_threshold_scenario", None)
        if shipping_country and threshold_scenario:
            from django_checkout.domain.customs_threshold import ABOVE_THRESHOLD
            from django_checkout.models.customs_threshold_config import CustomsThresholdConfig

            threshold_config = CustomsThresholdConfig.get_config(shipping_country)
            if (
                threshold_config
                and threshold_config.apply_threshold_to_shipping
                and threshold_scenario == ABOVE_THRESHOLD
            ):
                apply_threshold_to_shipping = True

    processed_data = []
    for item in base_data:
        item["price_brutto"] = (
            item["matrix_price_brutto"] if item["matrix_price_brutto"] is not None else item["price_brutto"]
        )

        item["price_brutto_modifier"] = (
            item["matrix_price_brutto_modifier"]
            if item["matrix_price_brutto_modifier"] is not None
            else Decimal("0.00").quantize(Decimal("0.01"))
        )

        so = options_by_pk.get(item["pk"])
        item["is_eglible_for_free_shipping"] = record.is_eglible_for_free_shipping_for_so(shipping_option=so)
        item["amount_missing_for_free_shipping"] = record.amount_missing_for_free_shipping_for_so(shipping_option=so)
        item["amount_missing_for_free_shipping_above_modifier"] = amount_missing_above_modifier

        if item["is_eglible_for_free_shipping"]:
            item["total_price"] = item["new_price"]
        elif item["amount_missing_for_free_shipping"] == 0:
            item["total_price"] = item["price_brutto_modifier"]
        elif item["amount_missing_for_free_shipping_above_modifier"] == 0:
            item["total_price"] = item["price_brutto"]
        else:
            item["total_price"] = item["price_brutto"] + item["price_brutto_modifier"]

        if apply_threshold_to_shipping:
            original_rate = Decimal(item["tax_rate"] or 0)
            item["total_price"] = round(item["total_price"] / (1 + original_rate), 2)
            item["tax_rate"] = Decimal(0)

        item["total_price_netto"] = round(item["total_price"] / (1 + item["tax_rate"]), 2)

        processed_data.append(item)

    # Remove country_code ALL if normal country_code is present
    count_all = [elem for elem in processed_data if elem["country_code"] == "ALL"]
    if len(count_all) > 0 and len(count_all) != len(processed_data):
        processed_data = [elem for elem in processed_data if elem["country_code"] != "ALL"]

    for elem in processed_data:
        elem["image"] = None if elem["image"] == "" else elem["image"]
        elem["image"] = make_media_url(elem["image"])
    return processed_data


@channel_view
@require_http_method("GET")
@authenticate
def get_shipping_methods_for_cart(request, cart_id, *args, **kwargs):
    """
    Can't handle more than one shipping method to count free delivery. It should be refactored.
    """
    record = Cart.objects.get_record(request, cart_id)
    if record is None:
        logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
        raise NotFound
    result = _build_shipping_methods_response(record, lang_override=request.GET.get("lang", None))
    pagination, paginated_data = paginate(request.GET, result)
    return PaginatedResponse(pagination, list(paginated_data))
