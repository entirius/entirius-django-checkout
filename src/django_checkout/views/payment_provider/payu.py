# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.domain.payment_provider.payu_signature import (
    SECOND_KEY_FIELD,
    SIGNATURE_HEADER,
    is_valid_notification,
)
from django_checkout.domain.reservation import release_stock_reservation
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Order, PaymentIntent

from ...bi import Checkout_PayuNotifyEvent

logger_process = ProcessLogger("PAYMENT_PAYU_PROVIDER_NOTIFY")


@csrf_exempt
@api_view
@require_http_method("POST")
def payu_notify(request: WSGIRequest, *args, **kwargs):
    """
    Fkcja payu_notify() jest wykonywana w przypadku, gdy człowiek opłaci dane zamówienie w PayU
    """
    bev = Checkout_PayuNotifyEvent(is_ongoing_event=True)
    # Validation: do we have valid json response?
    try:
        body = json.loads(request.body)
    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="Payu response error, body not in json format",
            details={"payu_response": request.body},
            error_message=str(e),
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    # Validation: do we have valid order_id in response?
    if "order" not in body or "orderId" not in body["order"]:
        extra = {"payu_response": body, "is_status_changed": False}
        logger_process.add_log_param("payu_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with response from Payu, can not find orderId in response body")
        bev.finish_with_error(finish_tag="Payu response error, can not find orderId in response body", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    # Validation: do we have valid PaymentIntent object?
    payu_order_id = body["order"]["orderId"]
    payment_intent: PaymentIntent = PaymentIntent.objects.filter(external_order_id=payu_order_id).first()
    if not payment_intent:
        extra = {"payu_order_id": payu_order_id, "payu_response": body, "is_status_changed": False}
        logger_process.add_log_param("payu_order_id", payu_order_id)
        logger_process.add_log_param("payu_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There is no PaymentIntent object with given order ID from PayU")
        bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
        # Not 200: this is usually a race — the notification outran the commit that creates
        # the PaymentIntent. PayU retries for 72h on any other code, so the confirmation
        # lands once the row exists. Answering 200 consumed it permanently.
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=503)

    # Authenticity check, before anything is written. Until this passes, every field in
    # `body` is attacker-controlled — including the status that marks an order paid.
    method = payment_intent.method
    second_key = (method.additional_data or {}).get(SECOND_KEY_FIELD) if method else None
    if not is_valid_notification(request.body, request.headers.get(SIGNATURE_HEADER), second_key):
        extra = {"payu_order_id": payu_order_id, "is_status_changed": False, "has_second_key": bool(second_key)}
        logger_process.add_log_param("payu_order_id", payu_order_id)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error(
            "PayU notification signature is invalid or unverifiable"
            if second_key
            else f"PayU PaymentMethod is missing '{SECOND_KEY_FIELD}' in additional_data — cannot verify notification"
        )
        bev.finish_with_error(finish_tag="PayU notification signature verification failed", details=extra)
        # Not 200, deliberately: PayU retries for 72h, so a channel whose second_key is not
        # configured yet gets a window to fix it instead of silently losing payments.
        return Response(data={"message": "Invalid signature"}, status="FAIL", status_code=403)

    # Validation: do we have valid status field in response?
    if "status" not in body["order"]:
        extra = {"payu_order_id": payu_order_id, "payu_response": body, "is_status_changed": False}
        logger_process.add_log_param("payu_order_id", payu_order_id)
        logger_process.add_log_param("payu_response", body)
        logger_process.add_log_param("is_status_changed", False)
        logger_process.error("There was a problem with the response body coming from Payu, can not find status field")
        bev.finish_with_error(finish_tag="Payu response error, can not find status field", details=extra)
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=200)

    # Business Logic
    payment_intent.provider_notify = body
    extra = {
        "order_id": payment_intent.order.order_id,
        "payment_intent_pk": payment_intent.pk,
        "payu_order_id": payu_order_id,
        "payment_status_prev": payment_intent.payment_status,
        "payment_status": None,
        "order_status_prev": payment_intent.order.order_status,
        "order_status": None,
        "is_status_changed": None,
    }
    logger_process.set_log_params({"details": extra})
    if body["order"]["status"] == "PENDING":
        # jesli payment jest PENDING
        # to nie ma to wpływu na zamówienie
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
        return Response(data={})

    elif body["order"]["status"] == "CANCELED":
        # jesli payment jest CANCELED
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

    elif body["order"]["status"] == "COMPLETED":
        # jesli payment jest COMPLETED
        # to ustawiamy status zamówienia na CONFIRMED
        payment_intent.payment_status = PaymentIntentStatus.COMPLETE
        payment_intent.save()
        order: Order = payment_intent.order
        order.order_status = OrderStatus.CONFIRMED
        # order.in_status_since = timezone.now() # jest autoustawianie Order.in_status_since w save()
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
        return Response(data={})

    # Nieobsluzone przypadki
    payment_intent.payment_status = PaymentIntentStatus.ERROR
    payment_intent.save()
    logger_process.add_log_param("is_status_changed", True)
    logger_process.add_log_param("order_status", payment_intent.order.order_status)
    logger_process.add_log_param("payment_status", payment_intent.payment_status)
    logger_process.add_log_param("unsupported_payment_status", body["order"]["status"])
    logger_process.error("Unsupported PayU payment_status, PaymentIntent status is set to error")
    extra["is_status_changed"] = True
    extra["order_status"] = payment_intent.order.order_status
    extra["payment_status"] = payment_intent.payment_status
    extra["unsupported_payment_status"] = body["order"]["status"]
    bev.finish_with_success(finish_tag="Unsupported PayU payment_status", details=extra)
    return Response(data={})
