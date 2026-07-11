# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import asdict
from typing import TYPE_CHECKING, Optional

from django.urls import reverse
from django.utils import timezone
from przelewy24_sdk.dto.payment import Additional, Shipping
from przelewy24_sdk.dto.payment import Cart as Przelewy24Cart
from przelewy24_sdk.dto.payment import TransactionRequest as Przelewy24Transaction
from przelewy24_sdk.dto.payment import VerifyRequest as Przelewy24Verify
from przelewy24_sdk.services.client import Przelewy24
from przelewy24_sdk.settings import CRC_KEY
from przelewy24_sdk.utils.sign import calculate_sign, calculate_verify_sign

from django_checkout import settings
from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.enums import PaymentIntentStatus
from django_checkout.utils import sanitize

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent

from process_logger import ProcessLogger, ProcessLoggerMixin


class Przelewy24PaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_PRZELEWY24_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    @staticmethod
    def _sandbox_setting(payment_method) -> bool:
        if "use_sandbox" in payment_method.additional_data and isinstance(
            payment_method.additional_data["use_sandbox"], bool
        ):
            return payment_method.additional_data["use_sandbox"]
        else:
            return True

    def authorize(self, przelewy24) -> Przelewy24:
        try:
            przelewy24.test_access()
            return przelewy24
        except Exception as e:
            self.logger.exception(e)
            self.error = True
            raise ConnectionError("Przelewy24 authorization failed")

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()

        przelewy24 = Przelewy24(
            client_id=self.payment_method.additional_data["client_id"],
            client_secret=self.payment_method.additional_data["client_secret"],
            sandbox=self._sandbox_setting(self.payment_method),
        )
        self.authorize(przelewy24)
        self.provider_request = self.build_przelewy24_data(przelewy24, order, cart)
        try:
            response_body, redirect_url_panel = przelewy24.create_transaction(transaction_data=self.provider_request)
            self.redirect_url = redirect_url_panel
            order_id = str(order.order_id)
            self.order_id = order_id
            return order_id
        except Exception as e:
            self.logger.exception(e)
            self.error = True
            return ""

    def verify_transaction_payment(self, response_body) -> dict:
        self.check_connection_data()

        przelewy24 = Przelewy24(
            client_id=self.payment_method.additional_data["client_id"],
            client_secret=self.payment_method.additional_data["client_secret"],
            sandbox=self._sandbox_setting(self.payment_method),
        )

        crc_key = self.payment_method.additional_data.get("crc_key", CRC_KEY)

        # Odczytanie odpowiedzi
        verify_request = self.build_verify_data(response_body, crc_key)
        try:
            result_verify = przelewy24.verify_transaction(verify_data=verify_request)
            if result_verify["data"]:
                self.logger.info(f"Transaction Verified Successfully. Response: {result_verify}")
                return result_verify
            else:
                self.logger.error(f"Transaction Verification Failed. Response: {result_verify}")
                return result_verify
        except Exception as e:
            self.logger.exception(e)
            self.error = True
            return {"error": e}

    def process_payment(self, payment: "PaymentIntent", **kwargs):
        payment.external_order_id = self.order_id
        payment.redirect_url = self.redirect_url
        payment.provider_notify = self.provider_notify
        if self.error:
            payment.payment_status = PaymentIntentStatus.ERROR
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = asdict(self.provider_request)
        sanitize(payment.provider_request)
        payment.save()
        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def map_przelewy24_transaction(
        self,
        przelewy24,
        order: "Order",
        przelewy24_cart: list[Przelewy24Cart],
        shipping: Additional,
        cart: Optional["Cart"] = None,
    ) -> Przelewy24Transaction:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order

        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )
        language = (
            order_dto.language_code.lower()
            if isinstance(order_dto.language_code, str)
            else self.payment_method.channel.default_language.iso2.lower()
        )
        amount = int(cart_dto.total * 100)
        pos_merchant_id = int(self.payment_method.additional_data["client_id"])
        regulation_accept = (
            self.payment_method.additional_data["regulation_accept"]
            if self.payment_method.additional_data["regulation_accept"]
            else False
        )
        crc_key = self.payment_method.additional_data.get("crc_key", CRC_KEY)
        session_id = str(order.order_id)
        sign = calculate_sign(
            session_id=session_id, merchant_id=pos_merchant_id, amount=amount, currency=currency, crc_key=crc_key
        )
        # Utwórz URL do endpointu przelewy24_notify
        url_status = "przelewy24-notify"
        full_url_status = self.get_notify_url(url_status)
        # Utwrz URL do return
        url_return = self.payment_method.additional_data["return_url"] + "?order_id=" + str(order.pretty_id)
        # Pobierz dostępne metody płatności
        payment_method_value = self.payment_method.additional_data.get("payment_method", None)

        if payment_method_value is not None:
            methods_response = przelewy24.get_payment_methods(language, amount, currency)
            methods = methods_response.get("data", [])
            for metod in methods:
                if metod["name"] == payment_method_value or metod["id"] == payment_method_value:
                    method = metod["id"]
                    break
            else:
                method = 0
        else:
            method = 0
        return Przelewy24Transaction(
            merchantId=pos_merchant_id,
            posId=pos_merchant_id,
            sessionId=session_id,
            amount=amount,
            currency=currency,
            description=order.channel.label,
            email=order_dto.addresses.billing_address.email,
            country=order_dto.addresses.shipping_address.country_code,
            language=language,
            urlReturn=url_return,
            sign=sign,
            client=f"{order_dto.addresses.billing_address.firstname} {order_dto.addresses.billing_address.lastname}",
            address=order_dto.addresses.shipping_address.street,
            zip=order_dto.addresses.shipping_address.postcode,
            city=order_dto.addresses.shipping_address.city,
            phone=order_dto.addresses.billing_address.telephone,
            method=method,
            urlStatus=full_url_status,
            timeLimit=0,
            channel=16,
            waitForResult=False,
            regulationAccept=regulation_accept,
            shipping=cart_dto.fee_price,
            transferLabel=order_dto.comment,
            encoding="UTF-8",
            methodRefId="",
            cart=przelewy24_cart,
            additional=shipping,
        )

    @staticmethod
    def map_przelewy24_shipping(order: "Order") -> Additional:
        order_dto = order.as_data
        shipping_type = 0
        # Types of shipping:
        # 0 - courier
        # 1 - point delivery
        # 2 - parcel machine
        # 3 - parcel in a store
        if order_dto.shipping_method.code == "courier" or "inpost":
            shipping_type = 0
        elif order_dto.shipping_method.code == "point_delivery":
            shipping_type = 1
        elif order_dto.shipping_method.code == "parcel_machine":
            shipping_type = 2
        elif order_dto.shipping_method.code == "parcel_in_a_store":
            shipping_type = 3

        return Additional(
            shipping=Shipping(
                type=shipping_type,
                address=order_dto.addresses.shipping_address.street,
                city=order_dto.addresses.shipping_address.city,
                zip=order_dto.addresses.shipping_address.postcode,
                country=order_dto.addresses.shipping_address.country_code,
            )
        )

    @staticmethod
    def map_przelewy24_cart(order: "Order", cart: Optional["Cart"] = None) -> list[Przelewy24Cart]:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order
        przelewy24_carts = []
        for cart_item in cart_dto.cart.items:
            przelewy24_cart = Przelewy24Cart(
                sellerId=order.channel.idx,
                sellerCategory=order.channel.label,
                name=cart_item.name if cart_item.name else cart_item.sku,
                description=getattr(cart_item, "description", ""),
                quantity=getattr(cart_item, "quantity", 0),
                price=getattr(cart_item, "price", 0),
                number=getattr(cart_item, "number", ""),
            )
            przelewy24_carts.append(przelewy24_cart)

        return przelewy24_carts

    def check_connection_data(self):
        if (
            "client_id" not in self.payment_method.additional_data
            or "client_secret" not in self.payment_method.additional_data
        ):
            raise AttributeError("No connection data in payment method")

    def build_przelewy24_data(self, przelewy24, order: "Order", cart: Optional["Cart"] = None) -> Przelewy24Transaction:
        przelewy24_cart = self.map_przelewy24_cart(order, cart)
        shipping = self.map_przelewy24_shipping(order)
        return self.map_przelewy24_transaction(przelewy24, order, przelewy24_cart, shipping, cart)

    @staticmethod
    def build_verify_data(response_body, crc_key) -> Przelewy24Verify:
        sign = calculate_verify_sign(
            response_body["sessionId"],
            response_body["orderId"],
            response_body["amount"],
            response_body["currency"],
            crc_key,
        )
        return Przelewy24Verify(
            merchantId=response_body["merchantId"],
            posId=response_body["posId"],
            sessionId=response_body["sessionId"],
            amount=response_body["amount"],
            currency=response_body["currency"],
            orderId=response_body["orderId"],
            sign=sign,
        )

    def get_notify_url(self, url_status):
        kwargs = {"version": settings.API_VERSION, "channel_idx": self.payment_method.channel.idx}
        return self.request.build_absolute_uri(reverse(url_status, kwargs=kwargs))
