# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.bi import Checkout_Przelewy24NotifyEvent
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Order, PaymentIntent

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

    if "orderId" not in body:
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
        verify = payment_intent.order.selected_payment_method.get_provider()
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
    payment_intent.payment_status = PaymentIntentStatus.COMPLETE
    payment_intent.save()
    order: Order = payment_intent.order
    order.order_status = OrderStatus.CONFIRMED
    order.save()
    extra["is_status_changed"] = True
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    bev.finish_with_success(finish_tag="PaymentIntent status completed", details=extra)
    return Response(data={"message": "PaymentIntent status completed"}, status="OK", status_code=200)
