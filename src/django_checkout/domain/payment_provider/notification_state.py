# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""What a gateway notification may still change on a payment and its order.

Gateways redeliver notifications for days and do not keep their order. Applied as they come, a
late one pulls a captured payment back to PENDING or ERROR, re-confirms an order that moved on
(``order_confirmed_signal`` fires again), or cancels an order an admin confirmed by hand and
releases its stock. The rules:

- a COMPLETE intent is final; a CANCELLED one only gives way to a completed payment;
- an order is confirmed only while it awaits payment, or after it was cancelled (paid wins,
  flagged for review because its stock reservation is gone);
- an order is cancelled only while it awaits payment.

Callers lock the intent and its order first (``lock_payment_intent`` inside
``transaction.atomic()``), so two deliveries of one event cannot both pass the checks.
"""

from dataclasses import dataclass

from process_logger import ProcessLogger

from django_checkout.domain.reservation import release_stock_reservation
from django_checkout.enums import OrderStatus, PaymentIntentStatus
from django_checkout.models import Order, PaymentIntent

AWAITING_PAYMENT_ORDER_STATUSES = (OrderStatus.NEW, OrderStatus.UNPAID)

# Wording the handlers always used in BI finish tags and log lines; BI reports group by it.
_INTENT_LABEL = {
    PaymentIntentStatus.PENDING: "PENDING",
    PaymentIntentStatus.APPROVED: "APPROVED",
    PaymentIntentStatus.CANCELLED: "CANCELED",
    PaymentIntentStatus.COMPLETE: "COMPLETE",
    PaymentIntentStatus.ERROR: "ERROR",
}

logger = ProcessLogger("PAYMENT_NOTIFICATION_STATE")


@dataclass(frozen=True)
class Outcome:
    """What ``apply_payment_status`` did."""

    message: str  # BI finish tag and log line
    applied: bool  # False: the rules ignored the status
    changed: bool  # the intent or the order status changed
    order_changed: bool


def lock_payment_intent(payment_intent: PaymentIntent) -> PaymentIntent:
    """The intent and its order re-read under a row lock. Call inside ``transaction.atomic()``."""
    return PaymentIntent.objects.select_for_update().select_related("order").get(pk=payment_intent.pk)


def ignored_change(payment_intent: PaymentIntent, payment_status: str) -> str | None:
    """Why ``payment_status`` must not be applied to ``payment_intent``, or None when it may."""
    current = payment_intent.payment_status
    final = current == PaymentIntentStatus.COMPLETE
    if final or (current == PaymentIntentStatus.CANCELLED and payment_status != PaymentIntentStatus.COMPLETE):
        return f"PaymentIntent is already {_label(current)}, {_label(payment_status)} ignored"
    return None


def confirm_order(order: Order) -> bool:
    """Set CONFIRMED when the order still waits for this payment; True when it changed."""
    if order.order_status not in (*AWAITING_PAYMENT_ORDER_STATUSES, OrderStatus.CANCELED):
        return False
    if order.order_status == OrderStatus.CANCELED:
        logger.critical(
            f"Paid order {order.pretty_id} was CANCELED before, its stock reservation had been released. "
            "MANUAL REVIEW REQUIRED - check product availability!"
        )
    order.order_status = OrderStatus.CONFIRMED
    order.save()
    return True


def cancel_order(order: Order) -> bool:
    """Cancel the order and release its stock while it still waits for payment; True when it changed."""
    if order.order_status not in AWAITING_PAYMENT_ORDER_STATUSES:
        return False
    release_stock_reservation(order)
    order.order_status = OrderStatus.CANCELED
    order.save()
    return True


def describe(payment_intent: PaymentIntent, order_changed: bool) -> str:
    """BI tag / log line for an applied status, e.g. "PaymentIntent status is set to COMPLETE, Order ..."."""
    payment_status = payment_intent.payment_status
    message = f"PaymentIntent status is set to {_label(payment_status)}"
    order_status = payment_intent.order.order_status
    if payment_status == PaymentIntentStatus.COMPLETE:
        return message + (
            ", Order status is set to CONFIRMED" if order_changed else f", Order status {order_status} kept"
        )
    if payment_status == PaymentIntentStatus.CANCELLED and not order_changed:
        return message + f", Order status {order_status} kept"
    return message


def apply_payment_status(payment_intent: PaymentIntent, payment_status: str) -> Outcome:
    """Set ``payment_status`` on the locked intent and move its order with it, within the rules.

    COMPLETE confirms the order and CANCELLED cancels it, each only when the order allows it;
    other statuses touch the intent alone.
    """
    ignored = ignored_change(payment_intent, payment_status)
    if ignored:
        return Outcome(ignored, applied=False, changed=False, order_changed=False)
    intent_changed = payment_intent.payment_status != payment_status
    payment_intent.payment_status = payment_status
    payment_intent.save()
    order_step = {PaymentIntentStatus.COMPLETE: confirm_order, PaymentIntentStatus.CANCELLED: cancel_order}.get(
        payment_status
    )
    order_changed = bool(order_step and order_step(payment_intent.order))
    return Outcome(
        describe(payment_intent, order_changed),
        applied=True,
        changed=intent_changed or order_changed,
        order_changed=order_changed,
    )


def _label(payment_status: str) -> str:
    return _INTENT_LABEL.get(payment_status, payment_status)
