# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING, Optional

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


class PayUCardPaymentProvider(PayUPaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_PAYUCARD_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def save_payment_method(self, order, order_dto, payu_order):
        self.logger.add_log_param_once("order_id", self.order_id)
        if order_dto.primary_payment_method.save_card:
            if not order_dto.primary_payment_method.card.startswith("TOK_"):
                self.logger.warning("Payment cant be processed. Token need to be MULTI, not SINGLE - save card")
                raise Exception("Wrong token type to save card card.")
        else:
            if order_dto.primary_payment_method.card.startswith("TOK_"):
                self.logger.warning("Payment cant be processed. Token need to be SINGLE, not MULTI - one-use card")
                raise Exception("Wrong token type to one-use card.")
        try:
            if order.customer and order_dto.primary_payment_method.save_card:
                from django_vault.models import (
                    Channel,
                    ChannelPayment,
                    CustomerPaymentVault,
                )

                channel_for_payment = ChannelPayment.objects.filter(
                    channel__idx=self.payment_method.channel.idx, provider=self.payment_method.provider
                ).first()
                channels = ChannelPayment.objects.filter(
                    channel__idx=self.payment_method.channel.idx, provider__startswith=self.payment_method.provider
                ).values_list("pk", flat=True)
                if not channel_for_payment:
                    self.logger.warning("Payment card won't be saved. ChannelPayment not found")
                    return payu_order

                customer_vault = CustomerPaymentVault.objects.filter(
                    customer__user__email=order.customer.email, channel_for_payment__channel__pk__in=channels
                ).first()
                if customer_vault is None:
                    customer_vault = CustomerPaymentVault(
                        customer=order.customer,
                        channel_for_payment=channel_for_payment,
                        first_transaction_id=order.pretty_id,
                    )
                    customer_vault.save()
                self.customer = customer_vault
                payu_order.cardOnFile = "FIRST"
            return payu_order
        except ImportError:
            self.logger.warning("Please install django-vault to save payment card method")
        except Exception as e:
            self.logger.exception(e)
        self.logger.delete_log_param("order_id")
        return payu_order

    def map_order(
        self, order: "Order", buyer: PayUBuyer, products: list[PayUProduct], cart: Optional["Cart"] = None
    ) -> PayUOrder:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order

        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        if order_dto.primary_payment_method.card is not None:
            pay_methods = PayUPayMethods.factory("CARD_TOKEN", order_dto.primary_payment_method.card)
        else:
            pay_methods = None

        payu_order = PayUOrder(
            extOrderId=order.pretty_id,
            notifyUrl=self.get_notify_url(),
            continueUrl=self.resolve_continue_url(
                order_dto.primary_payment_method.add_order_id_to_continue_url(order.pretty_id), order
            ),
            customerIp=self.get_client_ip(),
            merchantPosId=self.payment_method.additional_data["pos_id"],
            description=order.pretty_id,
            currencyCode=currency,
            totalAmount=self.price_to_lowest_value(cart_dto.total),
            buyer=buyer,
            products=products,
            payMethods=pay_methods,
        )
        payu_order = self.save_payment_method(order, order_dto, payu_order)
        return payu_order
