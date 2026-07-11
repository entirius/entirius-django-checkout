# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid
from dataclasses import asdict
from decimal import Decimal
from functools import cached_property
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone
from marshmallow import EXCLUDE

from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.shipping import get_delivery_matrix_prices
from django_checkout.enums import CartStatus, ItemStatus
from django_checkout.models.payment_method import PaymentMethod
from django_checkout.models.shipping_option import ShippingOption
from django_checkout.utils import anonymize_addresses_in_body, sanitize
from django_checkout.worker.split_order.validator import check_that_order_can_be_split

if TYPE_CHECKING:
    from django_accounts.models.customer import Customer

    from django_checkout.models.channel import Channel

from django_checkout.models.channel import DiscountApplyType
from django_checkout.models.managers.cart_manager import CartManager


class Cart(models.Model):
    objects = CartManager()
    channel: "Channel" = models.ForeignKey("Channel", null=True, on_delete=models.SET_NULL)
    customer: "Customer" = models.ForeignKey("django_accounts.Customer", null=True, on_delete=models.SET_NULL)
    cart_id = models.UUIDField(default=uuid.uuid4, editable=False)
    cart_status = models.CharField(max_length=20, default=CartStatus.NEW, choices=CartStatus.choices)
    in_status_since = models.DateTimeField(editable=False, default=timezone.now)
    cart_body = models.JSONField(null=True, blank=True)
    original_cart = models.ForeignKey("self", null=True, on_delete=models.SET_NULL, related_name="og_splitted_orders")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["cart_id"], name="unique_cart_id")]

    def save(self, *args, **kwargs):
        # If Order is being created the default argument does the job
        # If Order exists already, check if status changed
        if self.pk is not None:
            current = Cart.objects.get(pk=self.pk)
            self.in_status_since = timezone.now() if (current.cart_status != self.cart_status) else self.in_status_since

        super().save(*args, **kwargs)

    @classmethod
    def create(cls, data, channel: "Channel", customer=None, *args, **kwargs):
        if isinstance(data, CheckoutData):
            cart_body = sanitize(asdict(data))
        elif isinstance(data, dict):
            cart_body = sanitize(data)
        else:
            cart_body = {}

        return cls(cart_status=CartStatus.NEW, cart_body=cart_body, channel=channel, customer=customer, *args, **kwargs)

    def add_customer(self, customer: "Customer"):
        if self.customer is None and customer is not None:
            self.customer = customer
        return self

    def replace(self, data):
        self.cart_status = CartStatus.NEW
        self.cart_body = sanitize(asdict(data))
        return self

    @property
    def as_data(self) -> CheckoutData:
        return CheckoutData.Schema(unknown=EXCLUDE).load(self.cart_body)

    @property
    def validation_status(self):
        return self.as_data.validation_status

    @property
    def item_data_list(self):
        sku_qty_list = [SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in self.as_data.cart.items]
        return sku_qty_list

    @property
    def item_sku_list(self):
        sku_list = [item.sku for item in self.as_data.cart.items]
        return sku_list

    @property
    def total_to_base_on(self):
        match self.channel.discount_apply_type:
            case DiscountApplyType.AFTER:
                return self.as_data.total
            case DiscountApplyType.BEFORE:
                return self.as_data.total - self.as_data.total_tax
        return self.as_data.total

    @property
    def available_shipping_methods(self):
        data = self.as_data
        if data.addresses is not None and data.addresses.shipping_address is not None:
            country_code = (
                data.addresses.shipping_address.country_code
                if hasattr(data.addresses.shipping_address, "country_code")
                else data.country_code
            )
        else:
            available_country_code = (
                ShippingOption.objects.filter(currency__iso3=data.currency_code, method__channel=self.channel)
                .order_by("free_delivery_above_brutto")
                .values_list("country_code", flat=True)
            )
            if data.country_code in available_country_code:
                country_code = data.country_code
            else:
                country_code = available_country_code.first()

        return ShippingOption.objects.get_available_items(
            country_code,
            data.currency_code,
            self.channel,
            self.item_data_list,
            is_logged=True if self.customer else False,
        )

    @property
    def selected_shipping_method(self) -> ShippingOption:
        data = self.as_data
        if (
            data.addresses is not None
            and data.shipping_method is not None
            and data.addresses.shipping_address is not None
        ):
            selected = ShippingOption.objects.get_item(
                data.shipping_method.code,
                (
                    data.addresses.shipping_address.country_code
                    if hasattr(data.addresses.shipping_address, "country_code")
                    else data.country_code
                ),
                data.currency_code,
                self.channel,
                [SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in data.cart.items],
                is_logged=True if self.customer else False,
            )
            return selected
        else:
            return None

    @property
    def available_delivery_points(self):
        data = self.as_data
        method_item = self.selected_shipping_method
        if method_item is not None:
            country_code = (
                data.addresses.shipping_address.country_code.lower()
                if hasattr(data.addresses.shipping_address, "country_code")
                else data.country_code
            )
            # TODO better get_nearest_points
            return DeliveryPoint.objects.get_nearest_points(method_item.method, country_code)
        else:
            return DeliveryPoint.objects.none()

    @cached_property
    def shipping_method_for_free_shipping(self):
        """
        Returns shipping method to show free shipping amount and missing
        If cart don't have selected shipping method, it will return first available shipping method in channel.
        :return:
        """
        selected_shipping_method = self.selected_shipping_method
        if selected_shipping_method is not None:
            return selected_shipping_method
        else:
            return (
                ShippingOption.objects.select_related("method")
                .filter(currency__iso3=self.as_data.currency_code, method__channel=self.channel)
                .exclude(free_delivery_above_brutto=None)
                .order_by("free_delivery_above_brutto")
                .first()
            )

    @property
    def amount_required_for_free_shipping(self):
        return self._calculate_required_for_free_shipping(None)

    def amount_required_for_free_shipping_for_so(self, shipping_option_pk=None, shipping_option=None):
        return self._calculate_required_for_free_shipping(shipping_option_pk, shipping_option)

    def _calculate_required_for_free_shipping(self, shipping_option_pk, shipping_option=None):
        # If cart has selected shipping method, return free_delivery_above_brutto
        # Else get it from first available shipping method for channel
        if shipping_option is not None:
            so = shipping_option
        elif shipping_option_pk is not None:
            so = ShippingOption.objects.select_related("method").get(pk=shipping_option_pk)
        else:
            so = self.shipping_method_for_free_shipping
        if not so:
            return None

        fdi, fdim, free_delivery_above_price_matrix, fdapm, nsp, nspm = get_delivery_matrix_prices(
            so, self.item_data_list, self.channel
        )
        # jeżeli istnieje matrix to zwróć wartość z matrixa
        if free_delivery_above_price_matrix is not None:
            return free_delivery_above_price_matrix
        else:
            return so.free_delivery_above_brutto

    @property
    def amount_missing_for_free_shipping(self):
        return self._calculate_amount_missing(None)

    def amount_missing_for_free_shipping_for_so(self, shipping_option_pk=None, shipping_option=None):
        return self._calculate_amount_missing(shipping_option_pk, shipping_option)

    def _calculate_amount_missing(self, shipping_option_pk, shipping_option=None):
        data = self.as_data
        if shipping_option is not None or shipping_option_pk is not None:
            amount_required_for_free_shipping = self.amount_required_for_free_shipping_for_so(
                shipping_option_pk, shipping_option
            )
        else:
            amount_required_for_free_shipping = self.amount_required_for_free_shipping
        # If no amount required, you can't calc missing
        if amount_required_for_free_shipping is None:
            return None
        else:
            free_shipping_items_total_price = Decimal(0)
            # If shipping method selected, cart already knows about free delivery items
            # Else find them by first available shipping method for channel
            if data.shipping_method:
                free_delivery_items = data.shipping_method.free_delivery_items
            else:
                sm = self.shipping_method_for_free_shipping

                free_delivery_items, *_ = get_delivery_matrix_prices(sm, self.item_data_list, self.channel)
            # Sum total price of free delivery items
            for item in data.cart.get_valid_items():
                if item.sku in free_delivery_items:
                    free_shipping_items_total_price += (
                        item.total_price - item.discount_amount
                        if self.channel.is_discount_after_tax()
                        else item.total_price_netto - item.discount_amount_netto
                    )
            # Calc missing result
            result = amount_required_for_free_shipping - free_shipping_items_total_price
            return result if result > 0 else Decimal("0.00")

    @property
    def amount_required_for_free_shipping_above_modifier(self):
        data = self.as_data
        sm = self.shipping_method_for_free_shipping
        (
            free_delivery_items,
            free_delivery_items_modifier,
            free_delivery_above_price,
            free_delivery_above_price_modifier,
            new_shipping_price,
            new_shipping_price_modifier,
        ) = get_delivery_matrix_prices(sm, self.item_data_list, self.channel)
        return free_delivery_above_price_modifier

    @property
    def amount_missing_for_free_shipping_above_modifier(self):
        data = self.as_data
        amount_required_for_free_shipping_above_modifier = self.amount_required_for_free_shipping_above_modifier
        if amount_required_for_free_shipping_above_modifier is None:
            return None
        else:
            free_shipping_items_total_price = Decimal(0)
            # If shipping method selected, cart already knows about free delivery items for modifiers
            # Else find them by first available shipping method for channel
            if data.shipping_method:
                free_delivery_items_modifier = data.shipping_method.free_delivery_modifier_items
            else:
                sm = self.shipping_method_for_free_shipping
                (
                    free_delivery_items,
                    free_delivery_items_modifier,
                    free_delivery_above_price,
                    free_delivery_above_price_modifier,
                    new_shipping_price,
                    new_shipping_price_modifier,
                ) = get_delivery_matrix_prices(sm, self.item_data_list, self.channel)
            # Sum total price of free delivery items modifier
            for item in data.cart.get_valid_items():
                if item.sku in free_delivery_items_modifier:
                    free_shipping_items_total_price += (
                        item.total_price - item.discount_amount
                        if self.channel.is_discount_after_tax()
                        else item.total_price_netto - item.discount_amount_netto
                    )
            # Calc missing result
            result = amount_required_for_free_shipping_above_modifier - free_shipping_items_total_price
            return result if result > 0 else Decimal("0.00")

    @property
    def is_eglible_for_free_shipping(self):
        return self._is_eglible_for_free_shipping(None)

    def is_eglible_for_free_shipping_for_so(self, shipping_option_pk=None, shipping_option=None):
        return self._is_eglible_for_free_shipping(shipping_option_pk, shipping_option)

    def _is_eglible_for_free_shipping(self, shipping_option_pk=None, shipping_option=None):
        data = self.as_data
        free_shipping_in_discounts = any(
            True for elem in data.cart.discounts if elem.free_shipping and hasattr(data.cart, "discounts")
        )
        if free_shipping_in_discounts:
            return True
        if shipping_option is not None or shipping_option_pk is not None:
            if shipping_option is not None:
                so_code = shipping_option.method.code
            else:
                so_code = ShippingOption.objects.select_related("method").get(pk=shipping_option_pk).method.code
            amount_missing_for_free_shipping = self.amount_missing_for_free_shipping_for_so(
                shipping_option_pk, shipping_option
            )
            if so_code in [method for discount in data.cart.discounts for method in discount.free_shipping_methods]:
                return True
        else:
            amount_missing_for_free_shipping = self.amount_missing_for_free_shipping
        amount_missing_for_free_shipping_above_modifier = self.amount_missing_for_free_shipping_above_modifier
        if amount_missing_for_free_shipping == 0 and (
            amount_missing_for_free_shipping_above_modifier == 0
            or amount_missing_for_free_shipping_above_modifier is None
        ):
            return True

        return False

    def available_payment_methods(self, is_logged, group):
        data = self.as_data
        if (
            (data.addresses is not None and data.addresses.billing_address is None)
            or data.addresses is None
            or not data.currency_code
        ):
            result = PaymentMethod.objects.none()
        else:
            result = PaymentMethod.objects.filter(
                channel=self.channel,
                countries__iso2=data.addresses.billing_address.country_code.upper(),
                currencies__iso3=data.currency_code.upper(),
            )
        cash_on_delivery_available = (
            self.selected_shipping_method is not None and self.selected_shipping_method.cash_on_delivery_available
        )

        return (
            result.filter_by_cash_on_delivery(cash_on_delivery_available, self.selected_shipping_method)
            .get_only_pm_available_for_cart_products(self.item_data_list, self.channel, is_logged)
            .get_only_not_limited_pm([item.sku for item in self.item_data_list], self.channel, is_logged)
            .get_only_by_customer_group(group)
        )

    @property
    def is_egible_for_split(self):
        items_to_split, items_to_split_length = check_that_order_can_be_split(
            self, self.channel, skip_user_declaration=True
        )
        if items_to_split is None:
            return False
        if items_to_split_length > 1:
            return True
        return False

    @property
    def selected_payment_method(self):
        """First payment method (legacy single-method accessor)."""
        methods = self.selected_payment_methods
        return methods[0] if methods else None

    @property
    def selected_payment_methods(self):
        """All payment methods attached to this cart (e.g. voucher + PayU)."""
        from django_checkout.models.payment_method import resolve_payment_methods_from_data

        return resolve_payment_methods_from_data(self.channel, self.as_data.payment_method)

    @property
    def tax_rates(self):
        data = self.as_data
        tax_rates = {}
        for item in data.cart.get_valid_items():
            if item.status != ItemStatus.VALID:
                continue
            tax_rate = f"{item.tax_rate}"

            if item.total_tax_amount:
                if tax_rate not in tax_rates:
                    tax_rates[tax_rate] = Decimal("0.00")
                tax_rates[tax_rate] += item.total_tax_amount

        if data.shipping_method:
            tax_rate = f"{data.shipping_method.tax_rate}"

            if data.shipping_method.tax_amount:
                if tax_rate not in tax_rates:
                    tax_rates[tax_rate] = Decimal("0.00")
                tax_rates[tax_rate] += data.shipping_method.tax_amount

        if data.fee_tax_price and data.fee_tax_rate:
            tax_rate = f"{data.fee_tax_rate}"
            if data.fee_tax_price:
                if tax_rate not in tax_rates:
                    tax_rates[tax_rate] = Decimal("0.00")
                tax_rates[tax_rate] += data.fee_tax_price

        tax_total = Decimal("0.00")
        for rate, amount in tax_rates.items():
            tax_total += amount

        tax_rates["total"] = tax_total
        return tax_rates

    def anonymize(self) -> bool:
        body = self.cart_body or {}
        save = anonymize_addresses_in_body(body)
        if save:
            self.cart_body = sanitize(body)
            self.save()
        return save

    def __str__(self):
        channel_name = self.channel.label if self.channel is not None else ""
        return f"{self.pk} | {channel_name} | {self.cart_status}"
