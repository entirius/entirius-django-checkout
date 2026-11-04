# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

import stripe
from django.core.handlers.wsgi import WSGIRequest
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import api_view, require_http_method
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.domain.payment_provider.notification_state import apply_payment_status, lock_payment_intent
from django_checkout.enums import PaymentIntentStatus, PaymentProvider
from django_checkout.models import PaymentIntent, PaymentMethod

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
        pm = PaymentMethod.objects.filter(channel__idx=channel_idx, provider=PaymentProvider.STRIPE).first()
        endpoint_secret = ((pm.additional_data if pm else None) or {}).get("endpoint_secret")

        # Only the channel's own endpoint secret verifies an event; an empty one is no secret. The view
        # makes no Stripe API call, so it leaves the process-wide stripe.api_key alone.
        if not endpoint_secret:
            bev.finish_with_error(finish_tag="No api_key and endpoint_secret data in PaymentMethod Stripe")
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    except Exception as e:
        logger_process.exception(e)
        bev.finish_with_error(finish_tag="Stripe response error, body not in json format", error_message=str(e))
        return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
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
        # The session as plain JSON, from the body construct_event has just verified: since stripe 12 a
        # StripeObject is no dict, and provider_notify (a JSONField) cannot store it.
        stripe_checkout = json.loads(request.body)["data"]["object"]

        # Validation: do we have valid PaymentIntent object?
        stripe_order_id = stripe_checkout["id"]
        # The event was verified with the secret of the channel in the URL, so only a Stripe payment of
        # that channel may follow it.
        payment_intent: PaymentIntent = PaymentIntent.objects.filter(
            external_order_id=stripe_order_id, order__channel__idx=channel_idx, method__provider=PaymentProvider.STRIPE
        ).first()
        if not payment_intent:
            extra = {"stripe_order_id": stripe_order_id, "stripe_response": stripe_checkout, "is_status_changed": False}
            logger_process.add_log_param("stripe_order_id", stripe_checkout)
            logger_process.add_log_param("stripe_response", stripe_checkout)
            logger_process.add_log_param("is_status_changed", False)
            logger_process.error("There is no PaymentIntent object with given order ID from Stripe")
            bev.finish_with_error(finish_tag="Can not find related PaymentIntent object", details=extra)
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)

        # Business Logic, under the notification_state rules: Stripe redelivers events and does
        # not keep their order, and a late one must not undo a payment or touch an order that moved on.
        with transaction.atomic():
            payment_intent = lock_payment_intent(payment_intent)
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
            message, finish_tag = _apply_event(payment_intent, event.type, stripe_checkout.get("payment_status"), extra)
        extra["order_status"] = payment_intent.order.order_status
        extra["payment_status"] = payment_intent.payment_status
        bev.finish_with_success(finish_tag=finish_tag, details=extra)
        if "unsupported_payment_status" in extra:
            logger_process.error(message)
            return Response(data={"message": "For more information see log"}, status="FAIL", status_code=400)
        logger_process.info(message)
        return Response(data={})

    # Reszta eventów
    return Response(data={}, status="FAIL", status_code=400)


def _intent_status(event_type: str, checkout_payment_status: str | None) -> str | None:
    """PaymentIntent status a checkout session event stands for, or None when it is not handled."""
    if event_type == "checkout.session.expired":
        return PaymentIntentStatus.CANCELLED
    return {"paid": PaymentIntentStatus.COMPLETE, "unpaid": PaymentIntentStatus.PENDING}.get(checkout_payment_status)


def _apply_event(payment_intent: PaymentIntent, event_type: str, checkout_payment_status: str | None, extra: dict):
    """Apply the event to the locked intent and its order; returns (log message, BI finish tag)."""
    payment_status = _intent_status(event_type, checkout_payment_status)
    outcome = apply_payment_status(payment_intent, payment_status or PaymentIntentStatus.ERROR)
    extra["is_status_changed"] = outcome.changed
    if outcome.applied and payment_status is None:
        extra["unsupported_payment_status"] = checkout_payment_status
        return (
            "Unsupported Stripe payment_status, PaymentIntent status is set to error",
            "Unsupported Stripe payment_status",
        )
    return outcome.message, outcome.message
