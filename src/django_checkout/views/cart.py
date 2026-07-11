# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from _decimal import Decimal
from dataclasses import asdict
from functools import reduce

from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import (
    authenticate,
    parse_body,
    parse_parameters,
    require_authentication,
    require_http_method,
    save_ip_and_country,
)
from django_utils.api.exceptions import BadRequest, MethodNotAllowed, NotFound
from django_utils.api.responses import ErrorInfo, Response
from django_utils.settings import HEADER_COUNTRY
from process_logger import ProcessLogger

from django_checkout.domain.cart import process_cart
from django_checkout.domain.dto.cart import CartMerge, CartRequest, CartResponse, CheckoutData, ItemData
from django_checkout.enums import CartStatus
from django_checkout.models import Cart
from django_checkout.utils.api.decorators import channel_view

logger_process = ProcessLogger("CHECKOUT_CART")


def prepare_errors_and_messages(messages):
    response_errors = []
    response_messages = []

    for message in messages:
        if isinstance(message, str):
            response_messages.append(message)
        elif isinstance(message, ErrorInfo):
            response_errors.append(message)
            if message.code not in response_messages:
                response_messages.append(message.code)
        else:
            continue
    return response_errors, response_messages


# Providers that need a persisted Cart row to apply side-effects (e.g. voucher
# creates CartVoucher rows). Keep this set minimal — adding a provider here
# doubles the cost of `POST /carts/` for any cart using it.
_ATTACH_SIDE_EFFECT_PROVIDERS = frozenset({"voucher"})


def _has_attach_side_effect_pm(payment_method) -> bool:
    """True iff at least one payment method's code is in _ATTACH_SIDE_EFFECT_PROVIDERS.

    Accepts the parsed CartRequest.payment_method (a List[PaymentData] DTO).
    Backward-compatible with the legacy single-dict shape.
    """
    if not payment_method:
        return False
    pms = payment_method if isinstance(payment_method, list) else [payment_method]
    for pm in pms:
        code = pm.code if hasattr(pm, "code") else (pm.get("code") if isinstance(pm, dict) else None)
        if code in _ATTACH_SIDE_EFFECT_PROVIDERS:
            return True
    return False


@csrf_exempt
@channel_view
@authenticate
@require_http_method("POST")
@parse_body(CartRequest.Schema)
@save_ip_and_country
def post_cart(request, body: CartRequest, *args, **kwargs):
    customer = (
        request.user.customer if (request.user is not None and getattr(request.user, "is_customer", False)) else None
    )
    geo_country = request.headers.get(HEADER_COUNTRY, None)
    processed_data, messages = process_cart(request.channel, body, customer=customer, geo_country=geo_country)
    record = Cart.create(processed_data, request.channel, customer=customer)
    record.save()
    # Cart now exists in DB — re-process so payment providers can attach side-effects
    # to the persisted cart (e.g. VoucherPaymentProvider.attach_to_cart creates
    # CartVoucher rows). The first call had cart=None because the row didn't exist yet.
    #
    # Scope this expensive second pass to providers that ACTUALLY need a persisted
    # cart (today: voucher). Other providers (payu, banktransfer, etc.) are
    # idempotent w.r.t. cart_id and produce the same result on the first pass.
    # Doubling process_cart for every customer just to fix a voucher edge case is
    # a YAGNI violation — keep the fast path fast.
    if record.cart_id and _has_attach_side_effect_pm(body.payment_method):
        processed_data, attach_messages = process_cart(
            request.channel,
            body,
            customer=customer,
            geo_country=geo_country,
            cart=record,
            declarative_payment_method=True,
        )
        messages.extend(attach_messages)
        record = record.replace(processed_data)
        record.save()
    response_body = CartResponse(
        cart_id=record.cart_id,
        cart_status=record.cart_status,
        **record.cart_body,
        free_shipping=record.is_eglible_for_free_shipping,
        can_be_split=record.is_egible_for_split,
        amount_required_for_free_shipping=record.amount_required_for_free_shipping,
        amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
        amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
        amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
        min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
        tax_rates=record.tax_rates,
    )
    logger_process.info("Cart was successfully created.", extra={"details": {"cart_id": record.cart_id}})
    regional = {
        "language": processed_data.language_code,
        "currency": processed_data.currency_code,
        "country": processed_data.country_code,
    }
    errors, messages = prepare_errors_and_messages(messages)
    return (
        Response(data=asdict(response_body), status_code=201, status="CREATED", messages=messages)
        .add_regional(**regional)
        .add_errors(errors)
    )


@csrf_exempt
@channel_view
@authenticate
@require_http_method("GET")
def get_latest_cart(request, *args, **kwargs):
    record = Cart.objects.get_latest_by_customer(request)
    if record is None:
        logger_process.info("There is no cart with this customer.", extra={})
        raise NotFound

    response_body = CartResponse(
        cart_id=record.cart_id,
        cart_status=record.cart_status,
        **record.cart_body,
        free_shipping=record.is_eglible_for_free_shipping,
        can_be_split=record.is_egible_for_split,
        amount_required_for_free_shipping=record.amount_required_for_free_shipping,
        amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
        amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
        amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
        min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
        tax_rates=record.tax_rates,
    )
    regional = {
        "language": response_body.language_code,
        "currency": response_body.currency_code,
        "country": response_body.country_code,
    }
    return Response(asdict(response_body)).add_regional(**regional)


@csrf_exempt
@channel_view
@authenticate
@require_http_method("POST")
@require_authentication
@parse_parameters(CartMerge.Schema)
def merge_cart(request, params: CartMerge, quest_cart_id, *args, **kwargs):
    geo_country = request.headers.get(HEADER_COUNTRY, None)
    customer = request.user.customer if (request.user is not None and request.user.is_customer) else None
    customer_record = Cart.objects.get_latest_by_customer(request)
    quest_record = Cart.objects.get_quest_record(request, quest_cart_id)
    operation_success = False
    if customer_record is None:
        logger_process.info("There is no cart with this customer.", extra={})
        raise NotFound("No cart with this customer")

    if quest_record is None:
        logger_process.info(f"There is no cart with this cart_id: {quest_cart_id}.", extra={})
        raise NotFound("wrong quest_cart_id")

    checkout_data_quest = CheckoutData.Schema().load(quest_record.cart_body)
    checkout_data_customer = CheckoutData.Schema().load(customer_record.cart_body)
    items_customer = checkout_data_customer.cart.items
    items_quest = checkout_data_quest.cart.items
    items_customer_qty = [(each.sku, each.quantity) for each in items_customer]
    items_quest_qty = [(each.sku, each.quantity) for each in items_quest]
    body = CartRequest.factory()

    if params.operation_type == "override":
        items = items_quest_qty
        unique_tuples = reduce(lambda res, tpl: dict(res, **{tpl[0]: res.get(tpl[0], 0) + tpl[1]}), items, {})
        for each in unique_tuples.items():
            item = ItemData(sku=each[0], quantity=each[1], offer_price=None, extra=None, sub_items=None)
            body.cart.items.append(item)
        operation_success = True
    elif params.operation_type == "add":
        items = items_customer_qty + items_quest_qty
        unique_tuples = reduce(lambda res, tpl: dict(res, **{tpl[0]: res.get(tpl[0], 0) + tpl[1]}), items, {})
        for each in unique_tuples.items():
            item = ItemData(sku=each[0], quantity=each[1], offer_price=None, extra=None, sub_items=None)
            body.cart.items.append(item)
        operation_success = True
    if operation_success:
        processed_data, messages = process_cart(
            request.channel, body, checkout_data_customer, customer=customer, geo_country=geo_country
        )
        record = customer_record.replace(processed_data)
        quest_record.delete()
        record.save()

        response_body = CartResponse(
            cart_id=customer_record.cart_id,
            cart_status=customer_record.cart_status,
            **customer_record.cart_body,
            can_be_split=customer_record.is_egible_for_split,
            free_shipping=customer_record.is_eglible_for_free_shipping,
            amount_required_for_free_shipping=customer_record.amount_required_for_free_shipping,
            amount_missing_for_free_shipping=customer_record.amount_missing_for_free_shipping,
            amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
            amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
            min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
            tax_rates=record.tax_rates,
        )
        regional = {
            "language": response_body.language_code,
            "currency": response_body.currency_code,
            "country": response_body.country_code,
        }
        errors, messages = prepare_errors_and_messages(messages)
        return (
            Response(data=asdict(response_body), message="Cart modified successfully", messages=messages)
            .add_regional(**regional)
            .add_errors(errors)
        )
    else:
        raise BadRequest(message="Operation failed")


@csrf_exempt
@channel_view
@authenticate
@require_http_method("GET", "PUT", "PATCH", "DELETE")
def get_or_put_cart(request, cart_id, *args, **kwargs):
    geo_country = request.headers.get(HEADER_COUNTRY, None)

    @parse_body(CartRequest.Schema)
    def put_cart(request, cart_id, body: CartRequest, *args, **kwargs):
        record = Cart.objects.get_record(request, cart_id)

        customer = request.user.customer if (request.user is not None and request.user.is_customer) else None
        if record is None:
            logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
            raise NotFound
        else:
            processed_data, messages = process_cart(
                request.channel,
                body,
                customer=customer,
                geo_country=geo_country,
                cart=record,
                declarative_payment_method=True,
            )
            record = record.replace(processed_data)
            record.save()
            response_body = CartResponse(
                cart_id=record.cart_id,
                cart_status=record.cart_status,
                **record.cart_body,
                free_shipping=record.is_eglible_for_free_shipping,
                can_be_split=record.is_egible_for_split,
                amount_required_for_free_shipping=record.amount_required_for_free_shipping,
                amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
                amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
                amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
                min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
                tax_rates=record.tax_rates,
            )
            status = "FAIL" if messages != [] else "OK"
            if status == "FAIL":
                logger_process.info("Cart processing failed", extra={"details": {"cart_id": cart_id}})
            logger_process.info("Cart was successfully updated.", extra={"details": {"cart_id": record.cart_id}})
            regional = {
                "language": response_body.language_code,
                "currency": response_body.currency_code,
                "country": response_body.country_code,
            }
            errors, messages = prepare_errors_and_messages(messages)
            return (
                Response(data=asdict(response_body), status=status, messages=messages)
                .add_regional(**regional)
                .add_errors(errors)
            )

    def get_cart(request, cart_id, *args, **kwargs):
        record = Cart.objects.get_record(request, cart_id)
        if record is None:
            logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
            raise NotFound
        else:
            response_body = CartResponse(
                cart_id=record.cart_id,
                cart_status=record.cart_status,
                **record.cart_body,
                free_shipping=record.is_eglible_for_free_shipping,
                can_be_split=record.is_egible_for_split,
                amount_required_for_free_shipping=record.amount_required_for_free_shipping,
                amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
                amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
                amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
                min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
                tax_rates=record.tax_rates,
            )
            regional = {
                "language": response_body.language_code,
                "currency": response_body.currency_code,
                "country": response_body.country_code,
            }
            return Response(asdict(response_body)).add_regional(**regional)

    @parse_body(CartRequest.Schema)
    def update_cart(request, cart_id, body: CartRequest, *args, **kwargs):
        record = Cart.objects.get_record(request, cart_id)
        customer = request.user.customer if (request.user is not None and request.user.is_customer) else None
        if record is None:
            logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
            raise NotFound
        else:
            # Checkout data is all of cart information from PUT or CREATE in DB
            checkout_data = CheckoutData.Schema().load(record.cart_body)
            processed_data, messages = process_cart(
                request.channel,
                body,
                checkout_data,
                customer=customer,
                geo_country=geo_country,
                cart=record,
                declarative_payment_method=True,
            )
            record = record.replace(processed_data)
            record.save()
            response_body = CartResponse(
                cart_id=record.cart_id,
                cart_status=record.cart_status,
                **record.cart_body,
                free_shipping=record.is_eglible_for_free_shipping,
                can_be_split=record.is_egible_for_split,
                amount_required_for_free_shipping=record.amount_required_for_free_shipping,
                amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
                amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
                amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
                min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
                tax_rates=record.tax_rates,
            )
            status = "FAIL" if messages != [] else "OK"
            if status == "FAIL":
                logger_process.info("Cart processing failed", extra={"details": {"cart_id": cart_id}})
            logger_process.info("Cart was successfully updated.", extra={"details": {"cart_id": record.cart_id}})
            regional = {
                "language": response_body.language_code,
                "currency": response_body.currency_code,
                "country": response_body.country_code,
            }
            errors, messages = prepare_errors_and_messages(messages)
            return (
                Response(data=asdict(response_body), status=status, messages=messages)
                .add_regional(**regional)
                .add_errors(errors)
            )

    def close_cart(request, cart_id, *args, **kwargs):
        record = Cart.objects.get_record(request, cart_id)
        if record is None:
            logger_process.info("There is no cart with the given cart_id.", extra={"details": {"cart_id": cart_id}})
            raise NotFound
        else:
            record.cart_status = CartStatus.CLOSED
            record.save()
            response_body = CartResponse(
                cart_id=record.cart_id,
                cart_status=record.cart_status,
                **record.cart_body,
                free_shipping=record.is_eglible_for_free_shipping,
                can_be_split=record.is_egible_for_split,
                amount_required_for_free_shipping=record.amount_required_for_free_shipping,
                amount_missing_for_free_shipping=record.amount_missing_for_free_shipping,
                amount_required_for_free_shipping_above_modifier=record.amount_required_for_free_shipping_above_modifier,
                amount_missing_for_free_shipping_above_modifier=record.amount_missing_for_free_shipping_above_modifier,
                min_order_price=Decimal(record.channel.min_order_price).quantize(Decimal("0.01")),
                tax_rates=record.tax_rates,
            )
            regional = {
                "language": response_body.language_code,
                "currency": response_body.currency_code,
                "country": response_body.country_code,
            }
            return Response(data=asdict(response_body), message="Cart closed successfully").add_regional(**regional)

    if request.method == "PUT":
        return put_cart(request, cart_id, *args, **kwargs)
    elif request.method == "GET":
        return get_cart(request, cart_id, *args, **kwargs)
    elif request.method == "PATCH":
        return update_cart(request, cart_id, *args, **kwargs)
    elif request.method == "DELETE":
        return close_cart(request, cart_id, *args, **kwargs)
    else:
        raise MethodNotAllowed
