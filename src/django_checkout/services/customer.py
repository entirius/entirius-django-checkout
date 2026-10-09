# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import dataclass, field

from django.db.models import Q, QuerySet
from process_logger import ProcessLoggerMixin

from django_checkout.models import Cart, Channel, Order
from django_checkout.signals import customer_anonymized_signal


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


class CustomerService(ProcessLoggerMixin):
    def anonymize_customer(
        self, email: str, channel: Channel | None = None
    ) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        """Anonymise the orders and carts of ``email``: in ``channel`` only, or in every channel when None."""
        erasure = _Erasure()
        orders = Order.objects.filter(Q(customer__user__email=email) | Q(order_body__icontains=email))
        for order in _in_channel(orders, channel):
            if self._anonymize_order(order, erasure) and order.cart is not None:
                self._anonymize_cart(order.cart, erasure)
        carts = Cart.objects.filter(Q(customer__user__email=email) | Q(cart_body__icontains=email))
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
