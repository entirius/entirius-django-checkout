# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import re
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.urls import reverse
from django.utils import timezone
from paynow_sdk import PayNow
from paynow_sdk.dto.payment import Address, AllAddresses, Buyer, OrderItem, Phone
from paynow_sdk.dto.payment import Payment as PayNowPayment

from django_checkout import settings
from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent

from process_logger import ProcessLogger, ProcessLoggerMixin


class PayNowPaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        self.signature = None
        logger = ProcessLogger("PAYMENT_PAYNOW_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()
        self.provider_request: PayNowPayment = self.build_paynow_data(order, cart)
        try:
            paynow = PayNow(
                api_key=self.payment_method.additional_data["api_key"],
                signature_key=self.payment_method.additional_data["signature_key"],
                is_sandbox=self.get_use_sandbox_setting(),
            )

            if "redirect_url" in self.payment_method.additional_data:
                redirect_url = self.payment_method.additional_data["redirect_url"]
                redirect_url = redirect_url.replace("<order_id>", order.pretty_id)
                self.provider_request.continueUrl = self.resolve_continue_url(redirect_url, order)

            if "description" in self.payment_method.additional_data:
                self.provider_request.description = self.payment_method.additional_data["description"]

            order_id, redirect_url, signature = paynow.create_payment(self.provider_request)

            self.redirect_url = redirect_url
            self.signature = signature
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
        payment.signature = self.signature
        if self.error:
            payment.payment_status = PaymentIntentStatus.ERROR
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = self.provider_request.to_dict()
        payment.save()

        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def build_paynow_data(self, order: "Order", cart: Optional["Cart"] = None) -> PayNowPayment:
        return self.map_order_to_payment_data(order, cart)

    @staticmethod
    def calc_price(price: Decimal) -> int:
        return int(price * 100)

    def map_order_to_payment_data(self, order: "Order", cart: Optional["Cart"] = None) -> PayNowPayment:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order
        additional_info = {}
        dialling_code = None
        phone_number = None
        dialling_code_without_plus = None
        if order_dto.addresses.billing_address.dialling_code:
            dialling_code = order_dto.addresses.billing_address.dialling_code
            dialling_code.lstrip("0")
            if not order_dto.addresses.billing_address.dialling_code.startswith("+"):
                dialling_code = "+" + order_dto.addresses.billing_address.dialling_code
            else:
                dialling_code = dialling_code[1:]
                dialling_code = dialling_code.lstrip("0")
                dialling_code = "+" + dialling_code

            # prevent from sending dialling code with just one digit (+)
            if len(dialling_code) == 1:
                dialling_code = dialling_code + "00"

            dialling_code_without_plus = dialling_code.lstrip("+")
            if len(order_dto.addresses.billing_address.dialling_code) > 5:
                dialling_code = order_dto.addresses.billing_address.dialling_code[:5]

        if order_dto.addresses.billing_address.telephone:
            phone_number = order_dto.addresses.billing_address.telephone
            phone_number = re.sub(r"\D", "", phone_number)
            if dialling_code_without_plus:
                if phone_number.startswith(dialling_code_without_plus) and len(phone_number) > 9:
                    phone_number = phone_number[len(dialling_code_without_plus) :]
            if len(phone_number) > 10:
                phone_number = phone_number[-10:]

        if order_dto.addresses.billing_address.firstname:
            first_name = (
                order_dto.addresses.billing_address.firstname[:50]
                if isinstance(order_dto.addresses.billing_address.firstname, str)
                else None
            )
            lastname = (
                order_dto.addresses.billing_address.lastname[:50]
                if isinstance(order_dto.addresses.billing_address.lastname, str)
                else None
            )

            if first_name:
                additional_info["firstName"] = first_name

            if lastname:
                additional_info["lastName"] = lastname

        if (
            phone_number
            and dialling_code
            and len(phone_number) <= 10
            and len(dialling_code) <= 5
            and dialling_code.startswith("+")
        ):
            additional_info["phone"] = Phone(number=phone_number, prefix=dialling_code)
        else:
            self.logger.info(
                "Phone number or dialling code is not valid",
                extra={"phone_number": phone_number, "dialling_code": dialling_code, "order_id": order.order_id},
            )
            additional_info["phone"] = Phone(number="n/a", prefix="n/a")

        validity_time = settings.PAYNOW_VALIDITY_TIME_SECONDS or (
            (
                settings.PAYNOW_VALIDITY_TIME_ORDER_DAYS
                if isinstance(settings.PAYNOW_VALIDITY_TIME_ORDER_DAYS, int)
                else 10
            )
            * 24
            * 60
            * 60
        )

        address_info = AllAddresses(
            billing=Address(
                street=order_dto.addresses.billing_address.street,
                houseNumber="n/a",
                zipcode=order_dto.addresses.billing_address.postcode,
                city=order_dto.addresses.billing_address.city,
                country=order_dto.addresses.billing_address.country_code,
            ),
            shipping=Address(
                street=order_dto.addresses.shipping_address.street,
                houseNumber="n/a",
                zipcode=order_dto.addresses.shipping_address.postcode,
                city=order_dto.addresses.shipping_address.city,
                country=order_dto.addresses.shipping_address.country_code,
            ),
        )

        return PayNowPayment(
            amount=self.calc_price(cart_dto.total),
            currency=cart_dto.currency_code,
            description=self.payment_method.channel.label[:255],
            externalId=order.pretty_id,
            buyer=Buyer(
                email=str(order.customer.email) if order.customer else order_dto.addresses.billing_address.email,
                **additional_info,
                address=address_info,
            ),
            orderItems=[
                OrderItem(
                    name=str(item.sku),
                    quantity=int(item.quantity),
                    price=self.calc_price(item.total_price),
                    category="all",
                )
                for item in cart_dto.cart.items
            ],
            # validityTime in seconds
            validityTime=validity_time,
        )

    def check_connection_data(self):
        if (
            "api_key" not in self.payment_method.additional_data
            or "signature_key" not in self.payment_method.additional_data
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
        return self.request.build_absolute_uri(reverse("paynow-notify", kwargs=kwargs))
