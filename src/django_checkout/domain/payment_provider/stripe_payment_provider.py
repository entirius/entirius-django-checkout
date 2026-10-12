# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import stripe
from django.utils import timezone

from django_checkout.domain.payment_provider.base_payment_provider import (
    BasePaymentProvider,
)
from django_checkout.enums import PaymentIntentStatus

if TYPE_CHECKING:
    from django_checkout.models import Cart, Order, PaymentIntent

from process_logger import ProcessLogger, ProcessLoggerMixin


class StripePaymentProvider(BasePaymentProvider, ProcessLoggerMixin):
    def __init__(self, *args, **kwargs):
        logger = ProcessLogger("PAYMENT_STRIPE_PROVIDER")
        self.set_logger(logger)
        super().__init__(*args, **kwargs)

    def is_automatic_tax_enabled(self) -> bool:
        """
        Check if automatic tax is enabled in payment method additional_data.
        Defaults to True if not specified for backward compatibility.
        """
        return self.payment_method.additional_data.get("automatic_tax", True)

    def get_localized_name(self, key: str, language_code: str, default: str) -> str:
        """
        Get localized name from payment method configuration.

        Expected structure in additional_data:
        {
            "localized_names": {
                "shipping": {
                    "en": "Shipping",
                    "de": "Versand",
                    "pl": "Dostawa"
                },
                "payment_fee": {
                    "en": "Payment Method Fee",
                    "de": "Zahlungsgebühr",
                    "pl": "Opłata za metodę płatności"
                }
            }
        }

        Args:
            key: The key to look up (e.g., "shipping", "payment_fee")
            language_code: ISO language code (e.g., "en", "de", "pl")
            default: Default value if translation not found

        Returns:
            Localized name or default value
        """
        localized_names = self.payment_method.additional_data.get("localized_names", {})
        translations = localized_names.get(key, {})
        return translations.get(language_code, default)

    def ensure_stripe_api_key(self):
        """Ensure Stripe API key is set for API calls"""
        if not stripe.api_key and "api_key" in self.payment_method.additional_data:
            stripe.api_key = self.payment_method.additional_data["api_key"]

    def create_stripe_tax_rate(self, tax_rate_percent: Decimal) -> str | None:
        """
        Create a new tax rate in Stripe and save it to payment method mapping.

        Creates tax rates as inclusive to match tax_behavior='inclusive' in line items.
        This ensures that prices sent to Stripe already include tax, and Stripe will
        calculate the tax portion from the total amount.

        Args:
            tax_rate_percent: Tax rate as decimal (e.g., Decimal('0.23') for 23%)

        Returns:
            Stripe tax_rate ID (e.g., "txr_xxxxx") or None if creation fails
        """
        try:
            # Ensure API key is set
            self.ensure_stripe_api_key()

            # Convert to percentage for display (0.23 -> 23.0)
            percentage = float(tax_rate_percent) * 100

            # Create tax rate in Stripe as INCLUSIVE to match tax_behavior='inclusive'
            tax_rate = stripe.TaxRate.create(
                display_name=f"VAT {percentage:.0f}%",
                inclusive=True,
                percentage=percentage,
                description=f"Automatically created inclusive tax rate for {percentage:.0f}% VAT",
            )

            tax_rate_id = tax_rate.id

            tax_rate_key = str(tax_rate_percent)
            if "tax_rate_mapping" not in self.payment_method.additional_data:
                self.payment_method.additional_data["tax_rate_mapping"] = {}

            self.payment_method.additional_data["tax_rate_mapping"][tax_rate_key] = tax_rate_id
            self.payment_method.save(update_fields=["additional_data"])

            self.logger.info(
                f"Created Stripe inclusive tax rate: {tax_rate_id} for {percentage:.0f}% VAT",
                tax_rate_id=tax_rate_id,
                percentage=percentage,
            )

            return tax_rate_id

        except Exception as e:
            self.logger.exception(e)
            return None

    def get_stripe_tax_rate_id(self, tax_rate_percent: Decimal | None) -> str | None:
        """
        Get Stripe tax_rate ID for given tax rate percentage from payment method configuration.
        If not found in mapping, automatically creates a new tax rate in Stripe.

        Expected structure in additional_data:
        {
            "tax_rate_mapping": {
                "0.23": "txr_xxxxx",  # 23% VAT
                "0.08": "txr_yyyyy",  # 8% VAT
            }
        }

        Args:
            tax_rate_percent: Tax rate as decimal (e.g., Decimal('0.23') for 23%)

        Returns:
            Stripe tax_rate ID (e.g., "txr_xxxxx") or None if not found/created
        """
        if not tax_rate_percent or self.is_automatic_tax_enabled():
            return None

        tax_rate_mapping = self.payment_method.additional_data.get("tax_rate_mapping", {})
        tax_rate_key = str(tax_rate_percent)
        tax_rate_id = tax_rate_mapping.get(tax_rate_key)

        if not tax_rate_id:
            tax_rate_id = self.create_stripe_tax_rate(tax_rate_percent)

        return tax_rate_id

    def process_order(self, order: "Order", cart: Optional["Cart"] = None) -> str:
        self.check_connection_data()

        stripe.api_key = self.payment_method.additional_data["api_key"]

        try:
            self.provider_request = self.build_stripe_data(order, cart)
            result = stripe.checkout.Session.create(**self.provider_request)
            self.redirect_url = result["url"] if "url" in result else None
            self.order_id = result["id"] if "id" in result else None
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
        payment.save()

        order = payment.order
        order.in_status_since = timezone.now()
        order.save()

    def build_stripe_data(self, order: "Order", cart: Optional["Cart"] = None) -> dict:
        line_items = self.build_line_items(order, cart)

        result = {**self.map_order(order), "line_items": line_items, **self.map_buyer(order)}

        return result

    def map_buyer(self, order: "Order") -> dict:
        order_dto = order.as_data

        return {
            "customer_email": str(order.customer.email) if order.customer else order_dto.addresses.billing_address.email
        }

    def build_line_items(self, order: "Order", cart: Optional["Cart"] = None) -> list:
        """
        Build line_items array including products and payment fee for Stripe Checkout.

        Supports two tax modes:
        1. automatic_tax=True: Uses tax_behavior='inclusive' with Stripe automatic tax calculation
        2. automatic_tax=False: Uses tax_behavior='inclusive' with predefined inclusive tax_rates

        All prices are sent with tax_behavior='inclusive' meaning the unit_amount includes tax.
        When using tax_rates, they must be created with inclusive=True to correctly extract
        the tax portion from the total amount.
        """
        order_dto = cart.as_data if cart else order.as_data
        currency = (
            order_dto.currency_code.lower()
            if isinstance(order_dto.currency_code, str)
            else self.payment_method.channel.default_currency.iso3.lower()
        )

        line_items = []
        use_automatic_tax = self.is_automatic_tax_enabled()
        language_code = order_dto.language_code if hasattr(order_dto, "language_code") else "en"

        # Add product line items
        for product_dto in order_dto.cart.items:
            name = product_dto.name if product_dto.name and product_dto != "" else product_dto.sku
            line_item = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": name, "metadata": {"sku": product_dto.sku}},
                    "unit_amount": self.price_to_lowest_value(product_dto.unit_price - product_dto.discount_amount),
                    "tax_behavior": "inclusive",
                },
                "quantity": int(product_dto.quantity),
            }

            if not use_automatic_tax:
                tax_rate_id = self.get_stripe_tax_rate_id(product_dto.tax_rate)
                if tax_rate_id:
                    line_item["tax_rates"] = [tax_rate_id]

            line_items.append(line_item)

        # Add shipping as line item
        if order_dto.shipping_method and order_dto.shipping_method.total_price > 0:
            shipping_name = self.get_localized_name("shipping", language_code, "Shipping")
            shipping_line_item = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": shipping_name},
                    "unit_amount": self.price_to_lowest_value(order_dto.shipping_method.total_price),
                    "tax_behavior": "inclusive",
                },
                "quantity": 1,
            }

            if not use_automatic_tax:
                shipping_tax_rate = getattr(order_dto.shipping_method, "tax_rate", None)
                if shipping_tax_rate:
                    shipping_tax_rate_id = self.get_stripe_tax_rate_id(shipping_tax_rate)
                    if shipping_tax_rate_id:
                        shipping_line_item["tax_rates"] = [shipping_tax_rate_id]

            line_items.append(shipping_line_item)

        # Add payment fee as line item
        if order_dto.fee_price and order_dto.fee_price > 0:
            fee_name = self.get_localized_name("payment_fee", language_code, "Payment Method Fee")
            fee_line_item = {
                "price_data": {
                    "currency": currency,
                    "product_data": {"name": fee_name},
                    "unit_amount": self.price_to_lowest_value(order_dto.fee_price),
                    "tax_behavior": "inclusive",
                },
                "quantity": 1,
            }

            if not use_automatic_tax:
                fee_tax_rate_id = self.get_stripe_tax_rate_id(order_dto.fee_tax_rate)
                if fee_tax_rate_id:
                    fee_line_item["tax_rates"] = [fee_tax_rate_id]

            line_items.append(fee_line_item)

        return line_items

    def map_order(self, order: "Order") -> dict:
        """
        Build base order data for Stripe Checkout Session.

        Conditionally enables automatic_tax based on payment method configuration.
        """
        order_dto = order.as_data
        use_automatic_tax = self.is_automatic_tax_enabled()

        result = {
            "client_reference_id": order.pretty_id,
            "metadata": {"channel": order.channel.idx, "order_idx": str(order.order_id)},
            "mode": "payment",
            "ui_mode": "hosted",
            "success_url": self.resolve_continue_url(
                order_dto.primary_payment_method.add_order_id_to_continue_url(order.pretty_id), order
            ),
        }

        if use_automatic_tax:
            result["automatic_tax"] = {"enabled": True}

        return result

    def check_connection_data(self):
        if "api_key" not in self.payment_method.additional_data:
            raise AttributeError("No connection data in payment method")

    def price_to_lowest_value(self, price: Decimal) -> int:
        return int(price * 100)
