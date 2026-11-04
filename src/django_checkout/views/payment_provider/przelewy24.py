# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.bi import Checkout_Przelewy24NotifyEvent
from django_checkout.domain.payment_provider.notification_state import apply_payment_status, lock_payment_intent
from django_checkout.domain.payment_provider.przelewy24_notification import crc_key_for, is_valid_notification
from django_checkout.enums import PaymentIntentStatus, PaymentProvider
from django_checkout.models import PaymentIntent

logger_process = ProcessLogger("PAYMENT_PRZELEWY24_PROVIDER_NOTIFY")


@csrf_exempt
@api_view
@require_http_method("POST")
def przelewy24_notify(request: WSGIRequest, *args, **kwargs):
    """Funkcja przelewy24_notify() jest wykonywana w przypadku, gdy klient opłaci dane zamówienie w Przelewy24"""

    bev = Checkout_Przelewy24NotifyEvent(is_ongoing_event=True)
    try:
        body = json.loads(request.body)
    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="Przelewy24 response error, body not in json format",
            details={"przelewy24_response": request.body},
            error_message=str(e),
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=500)

    if "orderId" not in body or "sessionId" not in body:
        extra = {"przelewy24_response": body, "is_status_changed": False}
        logger_process.add_log_param("przelewy24_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with response from Przelewy24, can not find orderId in response body")
        bev.finish_with_error(
            finish_tag="Przelewy24 response error, can not find orderId in response body", details=extra
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=500)

    przelewy24_order_id = body["sessionId"]
    payment_intent: PaymentIntent = PaymentIntent.objects.filter(external_order_id=przelewy24_order_id).first()
    if not payment_intent:
        extra = {"przelewy24_order_id": przelewy24_order_id, "przelewy24_response": body, "is_status_changed": False}
        logger_process.add_log_param("przelewy24_order_id", przelewy24_order_id)
        logger_process.add_log_param("przelewy24_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given order ID from Przelewy24")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=500)

    # The sessionId comes from the request: it may name another gateway's payment.
    method = payment_intent.method
    if method is None or method.provider != PaymentProvider.PRZELEWY24:
        extra = {"przelewy24_order_id": przelewy24_order_id, "payment_intent_pk": payment_intent.pk}
        logger_process.add_log_param_once("przelewy24_order_id", przelewy24_order_id)
        logger_process.add_log_param_once("payment_intent_pk", payment_intent.pk)
        logger_process.error("PaymentIntent is not a Przelewy24 payment, notification ignored")
        bev.finish_with_error(finish_tag="Przelewy24 notification rejected", details=extra)
        return Response(data={"message": "Notification rejected"}, status="FAIL", status_code=403)

    # Authenticity check before Przelewy24 is asked: until it passes every field is caller-controlled.
    if not is_valid_notification(body, crc_key_for(method)):
        extra = {"przelewy24_order_id": przelewy24_order_id, "payment_intent_pk": payment_intent.pk}
        logger_process.add_log_param_once("przelewy24_order_id", przelewy24_order_id)
        logger_process.add_log_param_once("payment_intent_pk", payment_intent.pk)
        logger_process.error("Przelewy24 notification sign is invalid or cannot be verified (crc_key missing)")
        bev.finish_with_error(finish_tag="Przelewy24 notification sign verification failed", details=extra)
        return Response(data={"message": "Invalid signature"}, status="FAIL", status_code=403)

    payment_intent.provider_notify = body
    extra = {
        "order_id": payment_intent.order.order_id,
        "payment_intent_pk": payment_intent.pk,
        "przelewy24_order_id": przelewy24_order_id,
        "payment_status_prev": payment_intent.payment_status,
        "payment_status": None,
        "order_status_prev": payment_intent.order.order_status,
        "order_status": None,
        "is_status_changed": None,
    }
    logger_process.set_log_params({"details": extra})
    try:
        # The payment's own method: the order's first selected method may be another one (a voucher).
        verify = method.get_provider()
        # przypadku udanej weryfikacji dostajemy status "success" oraz responseCode: 0
        response_body = verify.verify_transaction_payment(body)
        if "data" not in response_body:
            logger_process.add_log_param("error_message", response_body["error"])
            logger_process.add_log_param("error_code", response_body["code"])
            logger_process.error("Przelewy24 verify transaction error")
            extra["error_message"] = response_body["error"]
            extra["error_code"] = response_body["code"]
            bev.finish_with_error(finish_tag="Przelewy24 verify transaction error", details=extra)
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=500)
    except Exception as e:
        logger_process.add_log_param("przelewy24_response", body)
        logger_process.exception(e)
        extra["przelewy24_response"] = body
        extra["error_message"] = str(e)
        bev.finish_with_error(finish_tag="Przelewy24 verify transaction error", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=500)
    # A resent notification verifies again at Przelewy24; it must not re-confirm an order that moved
    # on (that fires order_confirmed_signal again). Rules: notification_state.
    with transaction.atomic():
        payment_intent = lock_payment_intent(payment_intent)
        payment_intent.provider_notify = body
        outcome = apply_payment_status(payment_intent, PaymentIntentStatus.COMPLETE)
    extra["is_status_changed"] = outcome.changed
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    # BI reports group by finish tag: a confirmed order keeps the wording it always had.
    finish_tag = "PaymentIntent status completed" if outcome.order_changed else outcome.message
    bev.finish_with_success(finish_tag=finish_tag, details=extra)
    return Response(data={"message": "PaymentIntent status completed"}, status="OK", status_code=200)
