# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.utils import timezone
from paypal_sdk.dto.payment import (
    Address,
    Amount,
    ApplicationContext,
    Breakdown,
    PaymentSource,
    PurchaseUnits,
    Shipping,
    Tax,
    UnitAmount,
)
from paypal_sdk.dto.payment import Item as PayPalItem
from paypal_sdk.dto.payment import OrderRequest as PayPalOrder
from paypal_sdk.dto.payment import Payer as PayPalPayer
from paypal_sdk.services.client import PayPal
from process_logger import ProcessLogger, ProcessLoggerMixin

from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent


def get_use_sandbox_setting(payment_method) -> bool:
    if "use_sandbox" in payment_method.additional_data and isinstance(
        payment_method.additional_data["use_sandbox"], bool
    ):
        return payment_method.additional_data["use_sandbox"]
    else:
        return True


class PayPalPaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_PAYPAL_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()

        paypal = PayPal(
            client_id=self.payment_method.additional_data["client_id"],
            client_secret=self.payment_method.additional_data["client_secret"],
            is_sandbox=get_use_sandbox_setting(self.payment_method),
        )

        self.provider_request = self.build_paypal_data(order, cart)
        try:
            response_body, redirect_url = paypal.create_order(order_data=self.provider_request)
            self.redirect_url = redirect_url
            self.order_id = response_body.get("id", None)
            self.provider_notify = response_body
            return self.order_id
        except Exception as e:
            self.logger.add_log_param_once("order_id", order.order_id)
            self.logger.add_log_param_once("order_pretty_id", order.pretty_id)
            self.logger.exception(e)
            self.error = True
            return ""

    def process_payment(self, payment: "PaymentIntent", **kwargs):
        payment.external_order_id = self.order_id
        payment.redirect_url = self.redirect_url
        payment.provider_notify = self.provider_notify
        if self.error:
            payment.payment_status = PaymentIntentStatus.ERROR
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = self.provider_request.to_dict()
        payment.save()

        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def build_paypal_data(self, order: "Order", cart: Optional["Cart"] = None) -> PayPalOrder:
        buyer = self.map_payer(order)
        products = self.map_products(order, cart)
        shipping = self.map_shipping(order)
        return self.map_order(order=order, payer=buyer, items=products, shipping=shipping, cart=cart)

    @staticmethod
    def map_payer(order: "Order") -> PayPalPayer:
        order_dto = order.as_data
        return PayPalPayer(
            email_address=order_dto.addresses.billing_address.email,
            phone={"phone_number": {"national_number": order_dto.addresses.billing_address.telephone}},
            name={
                "given_name": order_dto.addresses.billing_address.firstname,
                "surname": order_dto.addresses.billing_address.lastname,
            },
        )

    def map_products(self, order: "Order", cart: Optional["Cart"] = None) -> list[PayPalItem]:
        # For split order
        order_dto = cart.as_data if cart else order.as_data

        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        products = []
        for product_dto in order_dto.cart.items:
            product = PayPalItem(
                name=product_dto.name,
                quantity=str(product_dto.quantity),
                sku=product_dto.sku,
                unit_amount=UnitAmount(value=str(product_dto.unit_price_netto), currency_code=currency),
                tax=Tax(value=str(product_dto.base_unit_tax_amount), currency_code=currency),
            )
            products.append(product)
        return products

    def create_application_context(self, order: "Order") -> ApplicationContext:
        order_pretty_id = order.pretty_id
        return ApplicationContext(
            return_url=self.resolve_continue_url(
                self.payment_method.additional_data["return_url"] + "?order_id=" + str(order_pretty_id), order
            ),
            cancel_url=self.resolve_continue_url(
                self.payment_method.additional_data["cancel_url"] + "?order_id=" + str(order_pretty_id), order
            ),
        )

    def map_order(
        self,
        order: "Order",
        payer: PayPalPayer,
        items: list[PayPalItem],
        shipping: Shipping,
        payment_source: PaymentSource = None,
        cart: Optional["Cart"] = None,
    ) -> PayPalOrder:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order

        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )
        return PayPalOrder(
            purchase_units=[
                PurchaseUnits(
                    custom_id=str(order.pretty_id),
                    items=items,
                    amount=Amount(
                        currency_code=currency,
                        value=str(cart_dto.total),
                        breakdown=Breakdown(
                            item_total=UnitAmount(currency_code=currency, value=str(cart_dto.cart.base_netto_price)),
                            tax_total=UnitAmount(
                                currency_code=currency,
                                value=str(
                                    Decimal(
                                        round(sum([item.base_total_tax_amount for item in order.as_data.cart.items]), 2)
                                    )
                                ),
                            ),
                            shipping=UnitAmount(
                                currency_code=currency, value=str(cart_dto.shipping_method.base_total_price)
                            ),
                            shipping_discount=UnitAmount(
                                currency_code=currency, value=str(cart_dto.shipping_method.discount_amount)
                            ),
                            discount=UnitAmount(currency_code=currency, value=str(cart_dto.cart.discount_amount)),
                            handling=UnitAmount(currency_code=currency, value=str(cart_dto.fee_price)),
                        ),
                    ),
                    shipping=shipping,
                )
            ],
            intent="CAPTURE",
            application_context=self.create_application_context(order),
            payer=payer,
            payment_source=payment_source,
        )

    @staticmethod
    def map_shipping(order: "Order") -> Shipping:
        order_dto = order.as_data
        return Shipping(
            type="SHIPPING",
            address=Address(
                address_line_1=order_dto.addresses.shipping_address.street,
                address_line_2=order_dto.addresses.shipping_address.street,
                admin_area_1=order_dto.addresses.shipping_address.city,
                admin_area_2=order_dto.addresses.shipping_address.city,
                postal_code=order_dto.addresses.shipping_address.postcode,
                country_code=order_dto.addresses.shipping_address.country_code.upper(),
            ),
        )

    def check_connection_data(self):
        if (
            "client_id" not in self.payment_method.additional_data
            or "client_secret" not in self.payment_method.additional_data
        ):
            raise AttributeError("No connection data in payment method")
