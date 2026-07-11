# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import logging
from decimal import Decimal

from django.db.models import BooleanField, Case, F, Q, Value, When
from django_regional.models import Currency
from django_utils.api.decorators import authenticate, require_http_method
from django_utils.api.exceptions import NotFound
from django_utils.api.responses import PaginatedResponse
from django_utils.api.utils import make_media_url
from process_logger import ProcessLogger

from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.domain.prices import calc_fee_of_price
from django_checkout.enums import FeeType, PaymentProvider
from django_checkout.models import Cart, PaymentMethod
from django_checkout.utils.api.decorators import channel_view
from django_checkout.utils.api.utils import paginate, resolve_language

logger = logging.getLogger(__name__)
logger_process = ProcessLogger("CHECKOUT_CART")


def _build_payment_methods_response(record, lang_override: str | None = None) -> list[dict]:
    """Build the list of available payment methods for a cart.

    Pure, request-independent: sources ``is_logged``/``group`` from the cart's
    own ``customer`` and language from the stored ``language_code`` (falling
    back to the channel default), so both the v1 function view and the v2 DRF
    view can share it. Returns the full, unpaginated list; callers paginate/wrap.
    """
    customer = record.customer
    is_logged = customer is not None
    group = customer.group if customer else None

    checkout_data = CheckoutData.Schema().load(record.cart_body)
    lang = resolve_language(record.channel, {"lang": lang_override, "checkout_lang": checkout_data.language_code})
    payment_method = record.available_payment_methods(is_logged, group).annotate(
        is_pay_code=Case(
            When(Q(provider__in=PaymentProvider.requires_code()), then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        )
    )
    data = list(
        payment_method.order_by("position", "pk").values(
            "pk",
            "code",
            "provider",
            "is_cash_on_delivery",
            "cash_on_delivery_fee",
            "image",
            "position",
            "is_pay_code",
            name=F(f"name_t9n__{lang}"),
            description=F(f"description_t9n__{lang}"),
        )
    )
    pk_list = [elem["pk"] for elem in data]
    method_list = PaymentMethod.objects.filter(pk__in=pk_list)
    extension_data = {}
    cart_data = record.as_data
    fee_data = {}
    total_price = cart_data.total
    currency = Currency.objects.filter(iso3=checkout_data.currency_code).first()
    if not currency:
        raise ValueError(f"There is no currency with code {checkout_data.currency_code}")
    for method in method_list:
        fee_extension = (
            " %"
            if method.fee_type == FeeType.PERCENTAGE_FEE
            else f" {currency.symbol}"
            if method.fee_type == FeeType.FLAT_FEE
            else ""
        )
        fee_total_cost = calc_fee_of_price(method, total_price)
        format_fee = f"{round(Decimal(method.fee_value), 2)}{fee_extension}"
        fee_data[method.pk] = {"fee": format_fee, "total_fee_price": fee_total_cost}
        try:
            # Pass the Cart record (not checkout_data): the base contract's arg is `cart`
            # and providers that use it need the persisted Cart — e.g. VoucherPaymentProvider
            # calls compute_applied_summary(cart.pk) to report applied voucher balance.
            extension = method.get_provider().get_payment_method_extension(record)
        except Exception as e:
            logger.exception(e)
            extension = {}
        extension_data[method.pk] = extension
    response_body = [{**elem, **extension_data[elem["pk"]], **fee_data[elem["pk"]]} for elem in data]
    for elem in response_body:
        elem.pop("pk")
        elem["image"] = None if elem["image"] == "" else elem["image"]
        elem["image"] = make_media_url(elem["image"])
    return response_body


@channel_view
@require_http_method("GET")
@authenticate
def get_payment_methods_for_cart(request, cart_id, *args, **kwargs):
    record = Cart.objects.get_record(request, cart_id)
    if record is None:
        logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
        raise NotFound
    result = _build_payment_methods_response(record, lang_override=request.GET.get("lang", None))
    pagination, paginated_data = paginate(request.GET, result)
    return PaginatedResponse(pagination, list(paginated_data))
