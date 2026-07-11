# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.urls import reverse
from django.utils import timezone
from payu_sdk import PayU
from payu_sdk.dto.buyer import Buyer as PayUBuyer
from payu_sdk.dto.order import Order as PayUOrder
from payu_sdk.dto.product import Product as PayUProduct

from django_checkout import settings
from django_checkout.domain.payment_provider.base_payment_provider import (
    BasePaymentProvider,
)
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent

from process_logger import ProcessLogger, ProcessLoggerMixin


class PayUPaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_PAYU_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()
        self.provider_request = self.build_payu_data(order, cart)

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
            order_id, redirect_url = payu.create_order(self.provider_request)
            if not redirect_url:
                self.logger.add_log_param_once("order_id", order.order_id)
                self.logger.add_log_param_once("order_pretty_id", order.pretty_id)
                self.logger.error("PayU create order failed, no redirect URL provided")
                self.error = True
                return ""
            self.redirect_url = redirect_url
            self.order_id = order_id
            return order_id
        except Exception as e:
            self.logger.add_log_param_once("order_id", order.order_id)
            self.logger.add_log_param_once("order_pretty_id", order.pretty_id)
            self.logger.exception(e)
            self.error = True
            return ""

    def process_payment(self, payment: "PaymentIntent", **kwargs):
        payment.external_order_id = self.order_id
        payment.redirect_url = self.redirect_url
        if self.error:
            payment.payment_status = PaymentIntentStatus.ERROR
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = asdict(self.provider_request)
        payment.save()

        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def build_payu_data(self, order: "Order", cart: Optional["Cart"] = None) -> PayUOrder:
        buyer = self.map_buyer(order)
        products = self.map_products(order, cart)
        return self.map_order(order, buyer, products, cart)

    def map_buyer(self, order: "Order") -> PayUBuyer:
        order_dto = order.as_data

        language = (
            order_dto.language_code.lower()
            if isinstance(order_dto.language_code, str)
            else self.payment_method.channel.default_language.iso2.lower()
        )

        return PayUBuyer(
            email=str(order.customer.email) if order.customer else order_dto.addresses.billing_address.email,
            phone=order_dto.addresses.billing_address.telephone,
            firstName=order_dto.addresses.billing_address.firstname,
            lastName=order_dto.addresses.billing_address.lastname,
            language=language,
        )

    def map_products(self, order: "Order", cart: Optional["Cart"] = None) -> list[PayUProduct]:
        # For split order
        order_dto = cart.as_data if cart else order.as_data

        products = []
        for product_dto in order_dto.cart.items:
            product = PayUProduct(
                name=product_dto.sku,
                unitPrice=self.price_to_lowest_value(product_dto.unit_price),
                quantity=str(product_dto.quantity),
            )
            products.append(product)
        return products

    def map_order(
        self, order: "Order", buyer: PayUBuyer, products: list[PayUProduct], cart: Optional["Cart"] = None
    ) -> PayUOrder:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order

        currency = (
            cart_dto.currency_code.upper()
            if isinstance(cart_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        return PayUOrder(
            extOrderId=order.pretty_id,
            notifyUrl=self.get_notify_url(),
            continueUrl=order_dto.primary_payment_method.add_order_id_to_continue_url(order.pretty_id),
            customerIp=self.get_client_ip(),
            merchantPosId=self.payment_method.additional_data["pos_id"],
            description=order.pretty_id,
            currencyCode=currency,
            totalAmount=self.price_to_lowest_value(cart_dto.total),
            buyer=buyer,
            products=products,
        )

    def price_to_lowest_value(self, price: Decimal) -> str:
        return str(int(price * 100))

    def check_connection_data(self):
        if (
            "client_id" not in self.payment_method.additional_data
            or "client_secret" not in self.payment_method.additional_data
            or "pos_id" not in self.payment_method.additional_data
        ):
            raise AttributeError("No connection data in payment method")

    def get_use_sandbox_setting(self) -> bool:
        if "use_sandbox" in self.payment_method.additional_data and isinstance(
            self.payment_method.additional_data["use_sandbox"], bool
        ):
            return self.payment_method.additional_data["use_sandbox"]
        else:
            return True

    def get_notify_url(self):
        kwargs = {"version": settings.API_VERSION, "channel_idx": self.payment_method.channel.idx}
        return self.request.build_absolute_uri(reverse("payu-notify", kwargs=kwargs))

    def get_client_ip(self):
        x_forwarded_for = self.request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0]
        else:
            ip = self.request.META.get("REMOTE_ADDR")
        return ip
