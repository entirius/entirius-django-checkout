# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from paynow_sdk import PayNow
from process_logger import ProcessLogger

from django_checkout.domain.payment_provider.notification_state import apply_payment_status, lock_payment_intent
from django_checkout.enums import PaymentIntentStatus
from django_checkout.models import PaymentIntent

from ...bi import Checkout_PayNowNotifyEvent

logger_process = ProcessLogger("PAYMENT_PAYU_PROVIDER_NOTIFY")


@csrf_exempt
@api_view
@require_http_method("POST")
def paynow_notify(request: WSGIRequest, *args, **kwargs):
    """
    Fukcja paynow_notify() jest wykonywana w przypadku, gdy użytkownik opłaci dane zamówienie w PayNow - Webhook
    """
    bev = Checkout_PayNowNotifyEvent(is_ongoing_event=True)
    # Validation: do we have valid json response?
    try:
        body = json.loads(request.body)
    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="PayNow response error, body not in json format",
            details={"paynow_response": request.body},
            error_message=str(e),
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)

    # Validation: do we have valid order_id in response?
    if "paymentId" not in body or "externalId" not in body:
        extra = {"paynow_response": body, "is_status_changed": False}
        logger_process.add_log_param("paynow_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with response from PayNow, can not find orderId in response body")
        bev.finish_with_error(finish_tag="PayNow response error, can not find orderId in response body", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)

    # Validation: do we have valid PaymentIntent object?
    paynow_order_id = body["paymentId"]
    payment_intent: PaymentIntent = PaymentIntent.objects.filter(external_order_id=paynow_order_id).first()
    if not payment_intent:
        extra = {"paynow_order_id": paynow_order_id, "paynow_response": body, "is_status_changed": False}
        logger_process.add_log_param("paynow_order_id", paynow_order_id)
        logger_process.add_log_param("paynow_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given order ID from PayNow")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)

    # Validation: do we have valid status field in response?
    if "status" not in body:
        extra = {"paynow_order_id": paynow_order_id, "paynow_response": body, "is_status_changed": False}
        logger_process.add_log_param("paynow_order_id", paynow_order_id)
        logger_process.add_log_param("paynow_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with the response body coming from PayNow, can not find status field")
        bev.finish_with_error(finish_tag="PayNow response error, can not find status field", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)
    try:
        paynow = PayNow(
            api_key=payment_intent.method.additional_data["api_key"],
            signature_key=payment_intent.method.additional_data["signature_key"],
        )
    except Exception as e:
        extra = {"paynow_order_id": paynow_order_id, "paynow_response": body, "is_status_changed": False}
        logger_process.add_log_param("paynow_order_id", paynow_order_id)
        logger_process.add_log_param("paynow_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.exception(e)
        bev.finish_with_error(finish_tag="Can not create PayNow object service", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)

    signature_header = request.headers.get("Signature")
    signature_calculated = paynow.authorization.calculate_signature(body=body, parameters={}, is_notification=True)

    if not signature_header or signature_calculated != signature_header:
        extra = {"paynow_order_id": paynow_order_id, "paynow_response": body, "is_status_changed": False}
        logger_process.add_log_param("paynow_order_id", paynow_order_id)
        logger_process.add_log_param("paynow_response", body)
        logger_process.add_log_param("is_status_changed", False)
        msg = "Signature is invalid" if not signature_header else "Signature is not equal calculated signature"
        logger_process.error(msg)
        bev.finish_with_error(finish_tag=msg, details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=404)

    # Business Logic
    with transaction.atomic():
        payment_intent = lock_payment_intent(payment_intent)
        extra = {
            "order_id": payment_intent.order.order_id,
            "payment_intent_pk": payment_intent.pk,
            "paynow_order_id": paynow_order_id,
            "payment_status_prev": payment_intent.payment_status,
            "payment_status": None,
            "order_status_prev": payment_intent.order.order_status,
            "order_status": None,
            "is_status_changed": None,
        }
        logger_process.set_log_params({"details": extra})
        payment_intent.provider_notify = body
        outcome = apply_payment_status(payment_intent, _INTENT_STATUS.get(body["status"], PaymentIntentStatus.ERROR))

    extra["is_status_changed"] = outcome.changed
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    logger_process.add_log_param("is_status_changed", outcome.changed)
    logger_process.add_log_param("order_status", payment_intent.order.order_status)
    logger_process.add_log_param("payment_status", payment_intent.payment_status)
    if outcome.applied and body["status"] not in _INTENT_STATUS:
        extra["unsupported_payment_status"] = body["status"]
        logger_process.add_log_param("unsupported_payment_status", body["status"])
        logger_process.error("Unsupported PayNow payment_status, PaymentIntent status is set to error")
        bev.finish_with_success(finish_tag="Unsupported PayNow payment_status", details=extra)
        return Response(data={})

    logger_process.info(outcome.message)
    bev.finish_with_success(finish_tag=_LEGACY_FINISH_TAG.get(outcome.message, outcome.message), details=extra)
    return Response(data={})


# Paynow status -> PaymentIntent status it stands for; anything else is an error.
_INTENT_STATUS = {
    "NEW": PaymentIntentStatus.PENDING,
    "PENDING": PaymentIntentStatus.PENDING,
    "REJECTED": PaymentIntentStatus.CANCELLED,
    "CONFIRMED": PaymentIntentStatus.COMPLETE,
}
# BI reports group by finish tag, and Paynow's confirmation always carried this wording.
_LEGACY_FINISH_TAG = {
    "PaymentIntent status is set to COMPLETE, Order status is set to CONFIRMED": (
        "PaymentIntent statuet to COMPLETE, Order status is set to CONFIRMED"
    ),
}
