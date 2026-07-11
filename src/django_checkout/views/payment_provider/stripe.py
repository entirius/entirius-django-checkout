# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


import stripe
from django.core.handlers.wsgi import WSGIRequest
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.domain.reservation import release_stock_reservation
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod

from ...bi import Checkout_StripeNotifyEvent

logger_process = ProcessLogger("PAYMENT_STRIPE_PROVIDER_NOTIFY")


@csrf_exempt
@api_view
@require_http_method("POST")
def stripe_notify(request: WSGIRequest, channel_idx: str = None, *args, **kwargs):
    """
    Fkcja stripe_notify() jest wykonywana w przypadku, gdy człowiek opłaci dane zamówienie w Stripe
    """

    bev = Checkout_StripeNotifyEvent(is_ongoing_event=True)

    try:
        endpoint_secret = None
        pm = PaymentMethod.objects.filter(channel__idx=channel_idx, provider=PaymentProvider.STRIPE).first()
        if pm and pm.additional_data:
            if "api_key" in pm.additional_data:
                stripe.api_key = pm.additional_data["api_key"]
            if "endpoint_secret" in pm.additional_data:
                endpoint_secret = pm.additional_data["endpoint_secret"]

        if not stripe.api_key and not endpoint_secret:
            bev.finish_with_error(finish_tag="No api_key and endpoint_secret data in PaymentMethod Stripe")
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(finish_tag="Stripe response error, body not in json format", error_message=str(e))
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    sig_header = request.META["HTTP_STRIPE_SIGNATURE"]
    try:
        event = stripe.Webhook.construct_event(request.body, sig_header, endpoint_secret)
    except ValueError as e:
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="Stripe response error", details={"stripe_response": request.body}, error_message=str(e)
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger_process.exception(e)
        bev.finish_with_error(
            finish_tag="Error verifying webhook signature",
            details={"stripe_response": request.body},
            error_message=str(e),
        )
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    if event.type in ["checkout.session.completed", "checkout.session.expired"]:
        stripe_checkout = event.data.object

        # Validation: do we have valid PaymentIntent object?
        stripe_order_id = stripe_checkout["id"]
        payment_intent: PaymentIntent = PaymentIntent.objects.filter(external_order_id=stripe_order_id).first()
        if not payment_intent:
            extra = {"stripe_order_id": stripe_order_id, "stripe_response": stripe_checkout, "is_status_changed": False}
            logger_process.add_log_param("stripe_order_id", stripe_checkout)
            logger_process.add_log_param("stripe_response", stripe_checkout)
            logger_process.add_log_param("is_status_changed", False)
            logger_process.error("There is no PaymentIntent object with given order ID from Stripe")
            bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

        # Business Logic
        payment_intent.provider_notify = stripe_checkout
        extra = {
            "order_id": payment_intent.order.order_id,
            "payment_intent_pk": payment_intent.pk,
            "stripe_order_id": stripe_order_id,
            "payment_status_prev": payment_intent.payment_status,
            "payment_status": None,
            "order_status_prev": payment_intent.order.order_status,
            "order_status": None,
            "is_status_changed": None,
        }
        logger_process.set_log_params({"details": extra})
        if event.type == "checkout.session.completed" and stripe_checkout["payment_status"] == "unpaid":
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

        elif event.type == "checkout.session.expired":
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

        elif event.type == "checkout.session.completed" and stripe_checkout["payment_status"] == "paid":
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
        logger_process.add_log_param("unsupported_payment_status", stripe_checkout["payment_status"])
        logger_process.error("Unsupported Stripe payment_status, PaymentIntent status is set to error")
        extra["is_status_changed"] = True
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        extra["unsupported_payment_status"] = stripe_checkout["payment_status"]
        bev.finish_with_success(finish_tag="Unsupported Stripe payment_status", details=extra)

        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    # Reszta eventów
    return Response(data={}, status="FAIL", status_code=400)
