# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import re
import unicodedata
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from autopay_sdk.dto.gateway import GatewayContext
from autopay_sdk.dto.order import Order as AutopayOrder
from autopay_sdk.services.client import Autopay
from autopay_sdk.utils.hash import generate_hash
from django.urls import reverse
from django.utils import timezone
from process_logger import ProcessLogger, ProcessLoggerMixin

from django_checkout import settings
from django_checkout.domain.payment_provider.base_payment_provider import (
    BasePaymentProvider,
)
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent


class AutopayPaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_AUTOPAY_PROVIDER")
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

    @staticmethod
    def _normalize_description(text: str, max_length: int = 79) -> str:
        """
        Normalize text for Autopay Description field according to API specification.
        Allowed characters: Latin alphanumeric (a-z, A-Z, 0-9) and . : - , space
        - Transliterates diacritic characters to Latin equivalents using Unicode NFKD normalization
        - Handles special cases that don't decompose (ł, ø, ß, etc.)
        - Removes characters not allowed by Autopay
        - Truncates to max_length (default 79 chars)
        """
        # Manual mapping for characters that don't decompose with NFKD
        special_mappings = {
            "ł": "l",
            "Ł": "L",
            "ø": "o",
            "Ø": "O",
            "đ": "d",
            "Đ": "D",
            "ð": "d",
            "Ð": "D",
            "þ": "th",
            "Þ": "Th",
            "ß": "ss",
            "æ": "ae",
            "Æ": "AE",
            "œ": "oe",
            "Œ": "OE",
        }
        for char, replacement in special_mappings.items():
            text = text.replace(char, replacement)

        # Decompose Unicode characters using NFKD (é → e, ą → a, etc.) and remove combining marks
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if unicodedata.category(char) != "Mn")

        # Keep only Latin alphanumeric and allowed special chars: . : - , space
        text = re.sub(r"[^a-zA-Z0-9.:\-, ]", "", text)

        # Remove multiple spaces and trim
        text = re.sub(r"\s+", " ", text).strip()

        # Truncate to max_length
        return text[:max_length]

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.provider_request = self.build_autopay_data(order, cart)

        autopay = Autopay(sandbox=self._sandbox_setting(self.payment_method))
        autopay.use_sandbox(self.get_use_sandbox_setting())

        try:
            response_body = autopay.create_order(self.provider_request)

            self.redirect_url = response_body["redirect_url"]
            self.order_id = response_body["order_id"]
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
        if self.error:
            payment.payment_status = PaymentIntentStatus.ERROR
        else:
            payment.payment_status = PaymentIntentStatus.PENDING
        payment.provider_request = self.provider_request
        payment.provider_notify = self.provider_notify
        payment.save()

        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def build_autopay_data(self, order: "Order", cart: Optional["Cart"] = None) -> AutopayOrder:
        return self.map_order(order, cart)

    def map_order(self, order: "Order", cart: Optional["Cart"] = None) -> AutopayOrder:
        order_dto = order.as_data
        cart_dto = cart.as_data if cart else order_dto  # For split order
        channel = self.payment_method.channel.idx

        # Sprawdź, czy istnieje odpowiedni service_id dla danego channel_idx
        if "service_id" in self.payment_method.additional_data:
            service_id_map = self.payment_method.additional_data["service_id"]
            # Sprawdź, czy channel_idx znajduje się w mapowaniu service_id
            if channel in service_id_map:
                service_id = service_id_map[channel]
            else:
                raise KeyError("Brak service_id dla danego channelu")
        else:
            raise KeyError("Brak service_id w additional_data")

        pretty_id = str(order.pretty_id)
        order_id = pretty_id  # uuid_str[:32]
        amount = str(cart_dto.total)
        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        # Build description with customer name (max 79 chars) for Autopay reports
        firstname = (
            getattr(order_dto.addresses.billing_address, "firstname", None)
            or getattr(order_dto.addresses.shipping_address, "firstname", None)
            or ""
        )
        lastname = (
            getattr(order_dto.addresses.billing_address, "lastname", None)
            or getattr(order_dto.addresses.shipping_address, "lastname", None)
            or ""
        )
        customer_name = f"{firstname} {lastname}"

        if customer_name:
            description = self._normalize_description(customer_name)
        else:
            description = self._normalize_description(self.payment_method.channel.label)

        gateway = self._get_gateway_id()
        customer_email = (
            str(order.customer.email) if order.customer is not None else order_dto.addresses.billing_address.email
        )

        hash_order = generate_hash(
            channel=channel,
            hash_key=settings.AUTOPAY_HASH_KEY,
            serviceID=service_id,
            orderID=order_id,
            amount=amount,
            Description=description,
            GatewayID=gateway,
            Currency=currency,
            CustomerEmail=customer_email,
            Title=self.payment_method.channel.label,
        )

        return AutopayOrder(
            ServiceID=service_id,
            OrderID=order_id,
            Amount=amount,
            Description=description,
            GatewayID=gateway,
            Currency=currency,
            CustomerEmail=customer_email,
            Title=self.payment_method.channel.label,
            Hash=hash_order,
        ).to_dict()

    def _get_gateway_id(self) -> int:
        """
        Resolve the Autopay GatewayID for this payment method.

        A GatewayID pre-selects the Autopay payment channel (BLIK, a specific bank, card, ...),
        so the customer skips the Autopay paywall and lands directly in that channel. Configure it
        per payment method via additional_data["gateway_id"] (values come from Autopay's gatewayList
        for the given ServiceID).

        When gateway_id is not configured the previous behaviour is preserved: the sandbox test
        gateway in sandbox, 0 (full Autopay paywall) in production.
        """
        gateway_id = self.payment_method.additional_data.get("gateway_id")
        if gateway_id is not None:
            return int(gateway_id)
        if self.get_use_sandbox_setting():
            return settings.SANDBOX_AUTOPAY_GATEWAY_ID
        return 0

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
        return self.request.build_absolute_uri(reverse("autopay-notify", kwargs=kwargs))

    def get_gateway(self, order: "Order"):
        order_dto = order.as_data
        channel = self.payment_method.channel.idx

        # Sprawdź, czy istnieje odpowiedni service_id dla danego channel_idx
        if "service_id" in self.payment_method.additional_data:
            service_id_map = self.payment_method.additional_data["service_id"]
            # Sprawdź, czy channel_idx znajduje się w mapowaniu service_id
            if channel in service_id_map:
                service_id = service_id_map[channel]
            else:
                raise KeyError("Brak service_id dla danego channelu")
        else:
            raise KeyError("Brak service_id w additional_data")

        # Convert UUID to string and remove hyphens
        uuid_str = str(order.order_id).replace("-", "")

        # Wygenerowanie hasza SHA-256
        hashed_id = hashlib.sha256(uuid_str.encode()).hexdigest()

        # Skrócenie hasza do 32 znaków
        message_id = hashed_id[:32]

        # message_id = str(order.order_id),
        currency = (
            order_dto.currency_code.upper()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.upper()
        )

        hash = generate_hash(
            channel=channel,
            hash_key=settings.AUTOPAY_HASH_KEY,
            serviceID=service_id,
            messageID=message_id,
            currencies=currency,
        )

        gateway_context = GatewayContext(ServiceID=service_id, MessageID=message_id, Hash=hash, Currencies=currency)
        autopay = Autopay(sandbox=self._sandbox_setting(self.payment_method))
        gateway_list = autopay.get_gateway_list(gateway_context)
        for gateway in gateway_list["gateway_list"]:
            for curr in gateway["currencyList"]:
                if curr["currency"] == currency:
                    return gateway.get("gatewayID")
