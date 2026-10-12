# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING, Optional

from payu_sdk import PayU
from payu_sdk.dto.buyer import Buyer as PayUBuyer
from payu_sdk.dto.order import Order as PayUOrder
from payu_sdk.dto.pay_method import PayMethods as PayUPayMethods
from payu_sdk.dto.product import Product as PayUProduct
from process_logger import ProcessLogger, ProcessLoggerMixin

from django_checkout.domain.payment_provider.payu_payment_provider import (
    PayUPaymentProvider,
)

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order


class PayUBlikPaymentProvider(PayUPaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_PAYU_BLIK_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def map_order(
        self, order: "Order", buyer: PayUBuyer, products: list[PayUProduct], cart: Optional["Cart"] = None
    ) -> PayUOrder:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto

        currency = (
            cart_dto.currency_code.upper()
            if isinstance(cart_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        blik_code = None
        if hasattr(order_dto.payment_method, "pay_code"):
            blik_code = order_dto.primary_payment_method.pay_code

        pay_methods = PayUPayMethods.factory("BLIK_AUTHORIZATION_CODE", blik_code) if blik_code else None

        return PayUOrder(
            extOrderId=order.pretty_id,
            notifyUrl=self.get_notify_url(),
            continueUrl=self.resolve_continue_url(
                order_dto.primary_payment_method.add_order_id_to_continue_url(order.pretty_id), order
            ),
            customerIp=self.get_client_ip(),
            merchantPosId=self.payment_method.additional_data["pos_id"],
            description=f"BLIK Order: {order.pretty_id}",
            currencyCode=currency,
            totalAmount=self.price_to_lowest_value(cart_dto.total),
            buyer=buyer,
            products=products,
            payMethods=pay_methods,
        )

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()

        order_dto = order.as_data

        if self.customer and self.customer.customer.user.email and self.customer.ext_customer_id:
            payu = PayU(
                client_id=self.payment_method.additional_data["client_id"],
                client_secret=self.payment_method.additional_data["client_secret"],
                pos_id=self.payment_method.additional_data["pos_id"],
                email=self.customer.customer.user.email,
                ext_customer_id=str(self.customer.ext_customer_id),
            )
        else:
            payu = PayU(
                client_id=self.payment_method.additional_data["client_id"],
                client_secret=self.payment_method.additional_data["client_secret"],
                pos_id=self.payment_method.additional_data["pos_id"],
            )
        payu.use_sandbox(self.get_use_sandbox_setting())
        payu.authorize()

        try:
            self.provider_request = self.build_payu_data(order, cart)
            order_id, *_ = payu.create_order(self.provider_request)
            self.redirect_url = order_dto.primary_payment_method.add_order_id_to_continue_url(order.pretty_id)
            self.order_id = order_id
            return order_id
        except Exception as e:
            self.logger.add_log_param_once("order_id", order.order_id)
            self.logger.add_log_param_once("order_pretty_id", order.pretty_id)
            self.logger.exception(e)
            self.error = True
            return ""
