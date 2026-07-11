# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db.models import Q
from process_logger import ProcessLoggerMixin

from django_checkout.models import Cart, Order


class CustomerService(ProcessLoggerMixin):
    def anonymize_customer(self, email: str) -> tuple[bool, list[str], list[str], list[str], list[str]]:
        success = True
        carts_idxs = []
        carts_fail = []
        orders_idxs = []
        orders_fail = []

        orders = Order.objects.filter(Q(customer__user__email=email) | Q(order_body__icontains=email))
        for order in orders:
            self.logger.add_log_param_once("order_id", order.pretty_id)
            self.logger.add_log_param_once("order_idx", str(order.order_id))
            try:
                if order.anonymize():
                    orders_idxs.append(str(order.order_id))
            except Exception as e:
                self.logger.exception(e)
                orders_fail.append(str(order.order_id))
                success = False
                continue
            else:
                self.logger.delete_few_log_param(["order_id", "order_idx"])

            if order.cart is not None:
                self.logger.add_log_param_once("cart_id", str(order.cart.cart_id))
                try:
                    if order.cart.anonymize():
                        carts_idxs.append(str(order.cart.cart_id))
                except Exception as e:
                    self.logger.exception(e)
                    carts_fail.append(str(order.cart.cart_id))
                    success = False
                    continue
                else:
                    self.logger.delete_log_param("cart_id")

        carts = Cart.objects.filter(Q(customer__user__email=email) | Q(cart_body__icontains=email))
        for cart in carts:
            self.logger.add_log_param_once("cart_id", str(cart.cart_id))
            try:
                if cart.anonymize():
                    carts_idxs.append(str(cart.cart_id))
            except Exception as e:
                self.logger.exception(e)
                carts_fail.append(str(cart.cart_id))
                success = False
                continue
            else:
                self.logger.delete_log_param("cart_id")

        orders_idxs = list(set(orders_idxs))
        carts_idxs = list(set(carts_idxs))
        orders_fail = list(set(orders_fail))
        carts_fail = list(set(carts_fail))
        return success, orders_idxs, carts_idxs, orders_fail, carts_fail
