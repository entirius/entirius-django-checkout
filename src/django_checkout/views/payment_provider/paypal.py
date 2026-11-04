# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import logging

from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, parse_parameters, require_http_method
from django_utils.api.exceptions import BadRequest
from django_utils.api.responses import Response
from paypal_sdk.services.client import PayPal
from process_logger import ProcessLogger

from django_checkout.domain.dto.paypal import PayPalPayment
from django_checkout.domain.payment_provider.notification_state import (
    Outcome,
    apply_payment_status,
    lock_payment_intent,
)
from django_checkout.domain.payment_provider.paypal_payment_provider import get_use_sandbox_setting
from django_checkout.domain.payment_provider.paypal_webhook import WEBHOOK_ID_FIELD, get_webhook_id, is_valid_webhook
from django_checkout.enums import PaymentIntentStatus, PaymentProvider
from django_checkout.models import PaymentIntent
from django_checkout.settings import PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK
from django_checkout.utils.gateway_payload import scrub_gateway_payload

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
    message = make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id)
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
        bev.finish_with_error(finish_tag="PayPal response error, body not in json format", error_message=str(e))
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    logged_body = scrub_gateway_payload(body)  # what logs and BI may keep of it: no payer data
    is_complete_payment_event = body.get("event_type", "") == "PAYMENT.CAPTURE.COMPLETED"

    # Validation: do we have valid order_id in response?
    if "resource" not in body or "id" not in body["resource"]:
        extra = {"paypal_response": logged_body, "is_status_changed": False}
        logger_process.add_log_param("paypal_response", logged_body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with response from PayPal, can not find orderId in response body")
        bev.finish_with_error(finish_tag="PayPal response error, can not find id in response body", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    if is_complete_payment_event:
        paypal_order_id = (
            body.get("resource", {}).get("supplementary_data", {}).get("related_ids", {}).get("order_id", None)
        )
        if not paypal_order_id:
            extra = {"paypal_response": logged_body, "is_status_changed": False}
            logger_process.add_log_param("paypal_response", logged_body)
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
        extra = {"paypal_order_id": paypal_order_id, "paypal_response": logged_body, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("paypal_response", logged_body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given if from PayPal")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    # Authenticity check, before anything is written. Until it passes, every field in `body` is
    # caller-controlled, the order id included.
    rejection = _rejection_reason(request, payment_intent)
    extra = {"paypal_order_id": paypal_order_id, "payment_intent_pk": payment_intent.pk, "is_status_changed": False}
    if rejection:
        _log_alone(logging.ERROR, rejection, extra)
        bev.finish_with_error(finish_tag="PayPal notification rejected", details=extra)
        # Not 200, deliberately: PayPal redelivers on any other code, so a genuine webhook rejected
        # while a method's webhook id is being configured lands once it is.
        return Response(data={"message": "Notification rejected"}, status="FAIL", status_code=403)

    # Validation: do we have valid status field in response?
    if "status" not in body["resource"]:
        extra = {"paypal_order_id": paypal_order_id, "payu_response": logged_body, "is_status_changed": False}
        logger_process.add_log_param("paypal_order_id", paypal_order_id)
        logger_process.add_log_param("paypal_response", logged_body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with the response body coming from PayPal, can not find status field")
        bev.finish_with_error(finish_tag="Payu response error, can not find status field", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    status = body["resource"]["status"]

    make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id)
    return Response(data={})


def _log_alone(level: int, message: str, params: dict) -> None:
    """One log line carrying only ``params``.

    The logger is shared by every request in the worker and add_log_param() keeps values, so a
    plain call would carry other requests' params, full request bodies included.
    """
    logger_process.reset_log_params()
    for key, value in params.items():
        logger_process.add_log_param_once(key, value)
    logger_process.log(level, message)


def _rejection_reason(request: WSGIRequest, payment_intent: PaymentIntent) -> str | None:
    """Why this notification must not touch ``payment_intent``, or None when PayPal sent it for it."""
    method = payment_intent.method
    if method is None or method.provider != PaymentProvider.PAYPAL:
        return "PaymentIntent is not a PayPal payment, notification ignored"
    if not get_webhook_id(method):
        return f"PayPal PaymentMethod is missing '{WEBHOOK_ID_FIELD}' in additional_data, cannot verify notification"
    if not is_valid_webhook(request.body, request.headers, method):
        return "PayPal webhook signature is invalid or could not be verified"
    return None


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
    # The cancel return is a GET anyone holding the token can repeat: it may only record that the
    # buyer gave up. A paid or approved PayPal order is settled by the return and the webhook.
    if status in _NOT_A_CANCELLATION:
        message = f"PayPal order is {status}, cancel return changes nothing"
        _log_alone(logging.WARNING, message, {**extra, "is_status_changed": False})
        bev.finish_with_success(finish_tag="Cancel return for a paid or approved PayPal order ignored", details=extra)
        return Response(message=message, data={"order_id": payment_intent.order.pretty_id}, status_code=200)

    # Business Logic
    payment_intent.provider_notify = response_body
    message = make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id)
    return Response(message=f"{message}", data={"order_id": payment_intent.order.pretty_id}, status_code=200)


def make_business_logic_for_status(request, status, payment_intent, extra, bev, paypal_order_id):
    """Apply a PayPal order status to ``payment_intent`` and its order; returns a message for the response.

    Guarded by ``notification_state``: a late or repeated status cannot undo a completed payment,
    re-confirm an order that moved on, or cancel one that no longer waits for payment.
    """
    provider_notify = payment_intent.provider_notify
    with transaction.atomic():
        payment_intent = lock_payment_intent(payment_intent)
        payment_intent.provider_notify = provider_notify
        outcome = apply_payment_status(payment_intent, _INTENT_STATUS.get(status, PaymentIntentStatus.ERROR))
        message = _finish_status(status, outcome, payment_intent, extra, bev)
    if outcome.applied and status == "APPROVED" and PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK:
        # Outside the lock: a slow PayPal must not hold the order row.
        _capture(payment_intent, paypal_order_id)
    return message


# PayPal order status -> PaymentIntent status it stands for.
_INTENT_STATUS = {
    "CREATED": PaymentIntentStatus.PENDING,
    "APPROVED": PaymentIntentStatus.APPROVED,
    "VOIDED": PaymentIntentStatus.CANCELLED,
    "COMPLETED": PaymentIntentStatus.COMPLETE,
}
_NOT_A_CANCELLATION = ("APPROVED", "COMPLETED")
# Returned to the storefront by return/ and cancel/, kept as they were.
_RESPONSE_MESSAGE = {
    "CREATED": "Order has been created but the payment has not been paid",
    "APPROVED": "Order has been created, approved but the payment has not been paid",
    "VOIDED": "Order has been cancelled",
    "COMPLETED": "Order has been created, approved and the payment has been paid",
}


def _finish_status(status: str, outcome: Outcome, payment_intent: PaymentIntent, extra: dict, bev) -> str:
    """Log and close the BI event for an applied (or ignored) status; returns the caller's response message."""
    if not outcome.applied:
        _finish(bev, extra, payment_intent, outcome.message, is_status_changed=False)
        return outcome.message
    if status not in _INTENT_STATUS:
        extra["unsupported_payment_status"] = status
        message = "Unsupported PayPal payment_status, PaymentIntent status is set to error"
        _finish(bev, extra, payment_intent, message, finish_tag="Unsupported PayPal payment_status")
        return "Unsupported status."
    _finish(bev, extra, payment_intent, outcome.message, is_status_changed=outcome.changed)
    return _RESPONSE_MESSAGE[status]


def _finish(bev, extra: dict, payment_intent: PaymentIntent, message: str, **outcome) -> None:
    """Log ``message`` and close the BI event; ``finish_tag`` defaults to the message."""
    extra["is_status_changed"] = outcome.get("is_status_changed", True)
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    level = logging.ERROR if "unsupported_payment_status" in extra else logging.INFO
    _log_alone(level, message, extra)
    bev.finish_with_success(finish_tag=outcome.get("finish_tag", message), details=extra)


def _capture(payment_intent: PaymentIntent, paypal_order_id: str) -> None:
    paypal = PayPal(
        client_id=payment_intent.method.additional_data["client_id"],
        client_secret=payment_intent.method.additional_data["client_secret"],
        is_sandbox=get_use_sandbox_setting(payment_intent.method),
    )
    try:
        paypal.get_capture_payment_for_order(paypal_order_id)
    except Exception as e:
        raise BadRequest(
            message="Something wrong with paypal capturing payment. See log",
            data={"order_id": payment_intent.order.pretty_id},
        ) from e
