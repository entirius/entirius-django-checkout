# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, parse_parameters, require_http_method
from django_utils.api.exceptions import BadRequest
from django_utils.api.responses import Response
from paypal_sdk.services.client import PayPal
from process_logger import ProcessLogger

from django_checkout.domain.dto.paypal import PayPalPayment
from django_checkout.domain.payment_provider.paypal_payment_provider import get_use_sandbox_setting
from django_checkout.domain.reservation import release_stock_reservation
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Order, PaymentIntent
from django_checkout.settings import PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK

from ...bi import Checkout_PayPalCancelEvent, Checkout_PayPalNotifyEvent, Checkout_PayPalReturnEvent

logger_process = ProcessLogger("PAYMENT_PAYPAL_PROVIDER_NOTIFY")


@csrf_exempt
@api_view
@require_http_method("GET")
@parse_parameters(PayPalPayment.Schema)
def paypal_return(request: WSGIRequest, params: PayPalPayment, *args, **kwargs):
    """
    Funkcja paypal_return() jest wykonywana w przypadku, gdy klient zatwierdził płatność w Paypal.
    """
    paypal_order_id = params.token
    bev = Checkout_PayPalReturnEvent(is_ongoing_event=True)
    payment_intent = PaymentIntent.objects.filter(external_order_id=paypal_order_id).first()
    if not payment_intent or payment_intent.method.code != "paypal":
        extra = {"paypal_order_id": paypal_order_id, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given order ID from PayPal")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    paypal = PayPal(
        client_id=payment_intent.method.additional_data["client_id"],
        client_secret=payment_intent.method.additional_data["client_secret"],
        is_sandbox=get_use_sandbox_setting(payment_intent.method),
    )

    extra = {
        "order_id": payment_intent.order.order_id,
        "payment_intent_pk": payment_intent.pk,
        "paypal_order_id": paypal_order_id,
        "payment_status_prev": payment_intent.payment_status,
        "payment_status": None,
        "order_status_prev": payment_intent.order.order_status,
        "order_status": None,
        "is_status_changed": None,
    }
    try:
        response_body, status = paypal.get_capture_payment_for_order(paypal_order_id)
    except:
        raise BadRequest(
            message="Something wrong with paypal capturing payment. See log",
            data={"order_id": payment_intent.order.pretty_id},
        )
    payment_intent.provider_notify = response_body
    message = make_business_logic_for_status(request, status, payment_intent, extra, bev)
    return Response(message=f"{message}", data={"order_id": payment_intent.order.pretty_id}, status_code=200)


@csrf_exempt
@api_view
@require_http_method("POST")
def paypal_notify(request: WSGIRequest, *args, **kwargs):
    """
    Funkcja paypal_notify() jest wykonywana w przypadku, gdy PayPal wysyła powiadomienie o zmianie statusu płatności.
    """
    bev = Checkout_PayPalNotifyEvent(is_ongoing_event=True)

    # Validation: do we have valid json response?
    try:
        body = json.loads(request.body)
    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="PayPal response error, body not in json format",
            details={"paypal_response": request.body},
            error_message=str(e),
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    is_complete_payment_event = body.get("event_type", "") == "PAYMENT.CAPTURE.COMPLETED"

    # Validation: do we have valid order_id in response?
    if "resource" not in body or "id" not in body["resource"]:
        extra = {"paypal_response": body, "is_status_changed": False}
        logger_process.add_log_param("paypal_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with response from PayPal, can not find orderId in response body")
        bev.finish_with_error(finish_tag="PayPal response error, can not find id in response body", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    if is_complete_payment_event:
        paypal_order_id = (
            body.get("resource", {}).get("supplementary_data", {}).get("related_ids", {}).get("order_id", None)
        )
        if not paypal_order_id:
            extra = {"paypal_response": body, "is_status_changed": False}
            logger_process.add_log_param("paypal_response", body)
            logger_process.add_log_param("is_status_changed", False)
            logger_process.error(
                "There was a problem with response from PayPal, can not find orderId in supplementary_data"
            )
            bev.finish_with_error(
                finish_tag="PayPal response error, can not find orderId in supplementary_data", details=extra
            )
            logger_process.error("PayPal response error, can not find related order_id in supplementary_data")
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)
    else:
        paypal_order_id = body["resource"]["id"]

    payment_intent: PaymentIntent = PaymentIntent.objects.filter(external_order_id=paypal_order_id).first()
    if not payment_intent:
        extra = {"paypal_order_id": paypal_order_id, "paypal_response": body, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("paypal_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given if from PayPal")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    # Validation: do we have valid status field in response?
    if "status" not in body["resource"]:
        extra = {"paypal_order_id": paypal_order_id, "payu_response": body, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("paypal_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with the response body coming from PayPal, can not find status field")
        bev.finish_with_error(finish_tag="Payu response error, can not find status field", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    status = body["resource"]["status"]

    make_business_logic_for_status(request, status, payment_intent, {}, bev, paypal_order_id)
    return Response(data={})


@csrf_exempt
@api_view
@require_http_method("GET")
@parse_parameters(PayPalPayment.Schema)
def paypal_cancel(request: WSGIRequest, params: PayPalPayment, *args, **kwargs):
    """
    Fukcja paypal_cancel() jest wykonywana w przypadku, gdy klient anuluje opłacanie zamówienia w PayPal
    https://developer.paypal.com/docs/api/orders/v2/#definition-order_status
    """
    bev = Checkout_PayPalCancelEvent(is_ongoing_event=True)
    paypal_order_id = params.token

    payment_intent = PaymentIntent.objects.filter(external_order_id=paypal_order_id).first()
    if not payment_intent or payment_intent.method.code != "paypal":
        extra = {"paypal_order_id": paypal_order_id, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given order ID from PayPal")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    paypal = PayPal(
        client_id=payment_intent.method.additional_data["client_id"],
        client_secret=payment_intent.method.additional_data["client_secret"],
        is_sandbox=get_use_sandbox_setting(payment_intent.method),
    )

    extra = {
        "order_id": payment_intent.order.order_id,
        "payment_intent_pk": payment_intent.pk,
        "paypal_order_id": paypal_order_id,
        "payment_status_prev": payment_intent.payment_status,
        "payment_status": None,
        "order_status_prev": payment_intent.order.order_status,
        "order_status": None,
        "is_status_changed": None,
    }
    try:
        response_body, status = paypal.get_order(paypal_order_id)
    except:
        raise BadRequest(
            message="Something wrong with paypal cancel. See log", data={"order_id": payment_intent.order.pretty_id}
        )
    # Business Logic
    payment_intent.provider_notify = response_body
    message = make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id)
    return Response(message=f"{message}", data={"order_id": payment_intent.order.pretty_id}, status_code=200)


def make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id):
    if status == "CREATED":
        # jesli order jest CREATE
        # to nie ma to wpływu na zamówienie, zamówienie w paypal jest założone,
        # ale nie zostało zatwierdzone, ani płatność nie została pobrana
        payment_intent.payment_status = PaymentIntentStatus.PENDING
        payment_intent.save()
        logger_process.add_log_param("is_status_changed", True)
        logger_process.add_log_param("order_status", payment_intent.order.order_status)
        logger_process.add_log_param("payment_status", payment_intent.payment_status)
        logger_process.info("PaymentIntent status is set to PENDING")
        extra["is_status_changed"] = True
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        bev.finish_with_success(finish_tag="PaymentIntent status is set to PENDING", details=extra)
        return "Order has been created but the payment has not been paid"

    elif status == "APPROVED":
        # jesli order jest APPROVED
        # to nie ma to wpływu na zamówienie, zamwówienie w paypal jest potwierdzone, ale płatność nie została pobrana
        payment_intent.payment_status = PaymentIntentStatus.APPROVED

        if PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK:
            # w takiej sytuacji wywołujemy pobranie płatności
            paypal = PayPal(
                client_id=payment_intent.method.additional_data["client_id"],
                client_secret=payment_intent.method.additional_data["client_secret"],
                is_sandbox=get_use_sandbox_setting(payment_intent.method),
            )

            try:
                response_body, status = paypal.get_capture_payment_for_order(paypal_order_id)
            except:
                raise BadRequest(
                    message="Something wrong with paypal capturing payment. See log",
                    data={"order_id": payment_intent.order.pretty_id},
                )
        payment_intent.save()
        logger_process.add_log_param("is_status_changed", True)
        logger_process.add_log_param("order_status", payment_intent.order.order_status)
        logger_process.add_log_param("payment_status", payment_intent.payment_status)
        logger_process.info("PaymentIntent status is set to APPROVED")
        extra["is_status_changed"] = True
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        bev.finish_with_success(finish_tag="PaymentIntent status is set to APPROVED", details=extra)
        return "Order has been created, approved but the payment has not been paid"

    elif status == "VOIDED":
        # jesli payment jest VOIDED
        # to zamówienie jest zamykane
        is_status_changed = payment_intent.payment_status != PaymentIntentStatus.CANCELLED
        payment_intent.payment_status = PaymentIntentStatus.CANCELLED
        if is_status_changed:
            release_stock_reservation(payment_intent.order)
        payment_intent.save()
        order: Order = payment_intent.order
        order.order_status = OrderStatus.CANCELED
        order.save()
        logger_process.add_log_param("is_status_changed", is_status_changed)
        logger_process.add_log_param("order_status", payment_intent.order.order_status)
        logger_process.add_log_param("payment_status", payment_intent.payment_status)
        logger_process.info("PaymentIntent status is set to CANCELED")
        extra["is_status_changed"] = is_status_changed
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        bev.finish_with_success(finish_tag="PaymentIntent status is set to CANCELED", details=extra)
        return Response(data={})

    elif status == "COMPLETED":
        # jesli payment jest COMPLETED
        # to zamówienie jest potwierdzone, a płatność została pobrana
        payment_intent.payment_status = PaymentIntentStatus.COMPLETE
        payment_intent.save()
        order: Order = payment_intent.order
        order.order_status = OrderStatus.CONFIRMED
        order.save()
        logger_process.add_log_param("is_status_changed", True)
        logger_process.add_log_param("order_status", payment_intent.order.order_status)
        logger_process.add_log_param("payment_status", payment_intent.payment_status)
        logger_process.info("PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED")
        extra["is_status_changed"] = True
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        bev.finish_with_success(
            finish_tag="PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED", details=extra
        )
        return "Order has been created, approved and the payment has been paid"

    # Nieobsluzone przypadki
    payment_intent.payment_status = PaymentIntentStatus.ERROR
    payment_intent.save()
    logger_process.add_log_param("is_status_changed", True)
    logger_process.add_log_param("order_status", payment_intent.order.order_status)
    logger_process.add_log_param("payment_status", payment_intent.payment_status)
    logger_process.add_log_param("unsupported_payment_status", status)
    logger_process.error("Unsupported PayPal payment_status, PaymentIntent status is set to error")
    extra["is_status_changed"] = True
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    extra["unsupported_payment_status"] = status
    bev.finish_with_success(finish_tag="Unsupported PayPal payment_status", details=extra)
    return "Unsupported status."
