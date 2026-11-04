# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
from dataclasses import dataclass, field

from django.db.models import Q, QuerySet
from process_logger import ProcessLoggerMixin

from django_checkout.enums import OrderStatus
from django_checkout.models import Cart, Channel, Order, UsedCoupon
from django_checkout.signals import customer_anonymized_signal

# The shop has nothing left to do with an order in these statuses: delivered, returned or cancelled. Any other one
# (waiting for payment, confirmed, on hold, in progress) still needs the delivery address and the contact, so an
# erasure is refused while the customer has such an order.
SETTLED_ORDER_STATUSES = (OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED)


@dataclass(frozen=True)
class BlockingOrder:
    order_id: str
    pretty_id: str | None
    status: str


class ErasureBlocked(Exception):
    """The customer has orders the shop has not fulfilled yet; nothing was anonymised."""

    def __init__(self, orders: list[BlockingOrder]):
        super().__init__(f"Erasure refused: {len(orders)} order(s) not fulfilled yet")
        self.orders = orders


@dataclass
class _Erasure:
    success: bool = True
    order_ids: list[str] = field(default_factory=list)
    orders_idxs: list[str] = field(default_factory=list)
    carts_idxs: list[str] = field(default_factory=list)
    orders_fail: list[str] = field(default_factory=list)
    carts_fail: list[str] = field(default_factory=list)

    def result(self) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        lists = (self.orders_idxs, self.carts_idxs, self.orders_fail, self.carts_fail)
        return self.success, *(list(set(ids)) for ids in lists)


def _in_channel(queryset: QuerySet, channel: Channel | None) -> QuerySet:
    return queryset if channel is None else queryset.filter(channel=channel)


def _refuse_unsettled(orders: list[Order]) -> None:
    blocking = [
        BlockingOrder(str(order.order_id), order.pretty_id_snap, order.order_status)
        for order in orders
        if order.order_status not in SETTLED_ORDER_STATUSES
    ]
    if blocking:
        raise ErasureBlocked(blocking)


def _in_body(email: str) -> str:
    """``email`` as a JSON string value: a body matches the whole address, never one that merely contains it."""
    return json.dumps(email, ensure_ascii=False)


def _orders_of(email: str) -> QuerySet:
    """Orders holding ``email`` anywhere checkout keeps it.

    The columns and the used codes find an order whose body an earlier erasure already anonymised, so a repeated
    request completes it; a used code also holds the account e-mail of a customer whose account is gone.
    """
    used_codes = UsedCoupon.objects.filter(Q(billing_email__iexact=email) | Q(shipping_email__iexact=email))
    return Order.objects.filter(
        Q(customer__user__email=email)
        | Q(billing_email__iexact=email)
        | Q(shipping_email__iexact=email)
        | Q(pk__in=used_codes.values("order_id"))
        | Q(order_body__icontains=_in_body(email))
    )


class CustomerService(ProcessLoggerMixin):
    def anonymize_customer(
        self, email: str, channel: Channel | None = None
    ) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        """Anonymise the orders and carts of ``email``: in ``channel`` only, or in every channel when None.

        Raises ``ErasureBlocked`` before anything is written while one of the orders is not settled yet.
        """
        orders = list(_in_channel(_orders_of(email), channel))
        _refuse_unsettled(orders)
        erasure = _Erasure()
        for order in orders:
            if self._anonymize_order(order, erasure) and order.cart is not None:
                self._anonymize_cart(order.cart, erasure)
        carts = Cart.objects.filter(Q(customer__user__email=email) | Q(cart_body__icontains=_in_body(email)))
        for cart in _in_channel(carts, channel):
            self._anonymize_cart(cart, erasure)
        self._notify(email, erasure, channel)
        return erasure.result()

    def _anonymize_order(self, order: Order, erasure: _Erasure) -> bool:
        """False when anonymising the order failed (its cart is then left alone)."""
        erasure.order_ids.append(str(order.order_id))
        self.logger.add_log_param_once("order_id", order.pretty_id)
        self.logger.add_log_param_once("order_idx", str(order.order_id))
        try:
            if order.anonymize():
                erasure.orders_idxs.append(str(order.order_id))
        except Exception as e:
            self.logger.exception(e)
            erasure.orders_fail.append(str(order.order_id))
            erasure.success = False
            return False
        self.logger.delete_few_log_param(["order_id", "order_idx"])
        return True

    def _anonymize_cart(self, cart: Cart, erasure: _Erasure) -> None:
        self.logger.add_log_param_once("cart_id", str(cart.cart_id))
        try:
            if cart.anonymize():
                erasure.carts_idxs.append(str(cart.cart_id))
        except Exception as e:
            self.logger.exception(e)
            erasure.carts_fail.append(str(cart.cart_id))
            erasure.success = False
            return
        self.logger.delete_log_param("cart_id")

    def _notify(self, email: str, erasure: _Erasure, channel: Channel | None) -> None:
        # send_robust so a failing downstream receiver (e.g. voucher PII scrub) can't
        # abort the erasure; a receiver exception still flips success=False.
        for _, response in customer_anonymized_signal.send_robust(
            sender=self.__class__,
            email=email,
            order_ids=list(set(erasure.order_ids)),
            channel_idx=channel.idx if channel is not None else None,
        ):
            if isinstance(response, Exception):
                self.logger.exception(response)
                erasure.success = False
