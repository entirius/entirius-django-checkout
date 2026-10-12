# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import models, transaction
from django.db.models import Max, Q
from django.utils import timezone
from django_pim.models import Product
from marshmallow import EXCLUDE

from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.dto.order import OrderData
from django_checkout.domain.product import fetch_products_feature, skus_have_attribute
from django_checkout.enums import CartStatus, OrderStatus, PaymentProvider, ValidationStatus
from django_checkout.models.item import Item
from django_checkout.models.managers.order_manager import OrderManager
from django_checkout.models.payment_intent import PaymentIntent
from django_checkout.models.payment_method import PaymentMethod
from django_checkout.models.product_representation import ProductRepresentation
from django_checkout.models.shipping import Shipping
from django_checkout.models.shipping_option import ShippingOption
from django_checkout.models.stock import Stock
from django_checkout.models.stock_reservation import StockReservation
from django_checkout.signals import order_canceled_signal, order_confirmed_signal, order_created_signal
from django_checkout.utils import anonymize_addresses_in_body, sanitize, scrub_payment_secrets_in_body

if TYPE_CHECKING:
    from django_accounts.models.customer import Customer

    from django_checkout.models.cart import Cart
    from django_checkout.models.channel import Channel


def _is_voucher_method(method: "PaymentMethod | None", code: str) -> bool:
    """Voucher discriminator — delegates to the single source of truth on the enum."""
    return PaymentProvider.is_voucher(method.provider if method is not None else None, code)


def _intent_amount(is_voucher: bool, data) -> Decimal:
    """Amount a payment method covers on this order.

    Voucher methods cover the applied voucher total; every other method covers
    data.total, which is already voucher-netted — the two always sum to the
    order's base total.
    """
    if is_voucher:
        return Decimal(str(getattr(data.cart, "voucher_amount_applied", None) or 0))
    return Decimal(str(data.total or 0))


class Order(models.Model):
    objects = OrderManager()
    channel: "Channel" = models.ForeignKey("Channel", null=True, on_delete=models.SET_NULL)
    customer: "Customer" = models.ForeignKey(
        "django_accounts.Customer", null=True, on_delete=models.SET_NULL, blank=True
    )
    billing_email = models.TextField(blank=True, null=True)
    shipping_email = models.TextField(blank=True, null=True)
    cart: "Cart" = models.ForeignKey("Cart", null=True, on_delete=models.PROTECT)
    order_status = models.CharField(max_length=20, default=OrderStatus.UNPAID, choices=OrderStatus.choices)
    in_status_since = models.DateTimeField(editable=False, default=timezone.now)
    order_id = models.UUIDField(default=uuid.uuid4, editable=False)
    in_channel_id = models.PositiveIntegerField(null=True)
    order_body = models.JSONField()
    split_orders = models.ManyToManyField("self", through="SplitOrderLink")
    extra = models.JSONField(null=True, blank=True, default=dict)
    preorder = models.BooleanField(default=False)
    preorder_date = models.DateTimeField(null=True, blank=True)
    pretty_id_snap = models.CharField(max_length=10, unique=True, null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cart"], name="one_cart_per_order"),
            models.UniqueConstraint(fields=["order_id"], name="unique_order_id"),
            models.UniqueConstraint(
                fields=["channel", "in_channel_id"],
                name="in_channel_id_unique_per_channel",
                condition=Q(in_channel_id__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "billing_email", "order_status"], name="idx_order_billing_email"),
            models.Index(fields=["channel", "shipping_email", "order_status"], name="idx_order_shipping_email"),
        ]

    def save(self, change_status_only_in_split_order=True, *args, **kwargs):
        is_status_changed_to_confirmed = False
        is_status_changed_to_canceled = False

        # If Order is being created the default argument does the job
        # If Order exists already, check if status changed
        if self.pk is not None:
            current = Order.objects.get(pk=self.pk)
            self.in_status_since = (
                timezone.now() if (current.order_status != self.order_status) else self.in_status_since
            )
            if current.order_status != OrderStatus.CONFIRMED and self.order_status == OrderStatus.CONFIRMED:
                is_status_changed_to_confirmed = True
            if current.order_status != OrderStatus.CANCELED and self.order_status == OrderStatus.CANCELED:
                is_status_changed_to_canceled = True

            if (
                current.order_status != self.order_status
                and current.split_orders.exists()
                and change_status_only_in_split_order
            ):
                for split_order in current.split_orders.all():
                    split_order.modify_status(self.order_status)
                if current.order_status == OrderStatus.CANCELED:
                    self.order_status = current.order_status

        if self.in_channel_id is None:
            max_id = Order.objects.filter(channel=self.channel).aggregate(max_id=Max("in_channel_id"))["max_id"]
            self.in_channel_id = 1 if max_id is None else max_id + 1

        super().save(*args, **kwargs)

        # after main save, bcs its need create_at timestamp
        if self.pretty_id_snap is None:
            self.pretty_id_snap = self.pretty_id

        super().save(update_fields=["pretty_id_snap"])

        if is_status_changed_to_confirmed:
            order_confirmed_signal.send(sender=Order.__class__, order_id=self.order_id)
        if is_status_changed_to_canceled:
            order_canceled_signal.send(sender=Order.__class__, order_id=self.order_id)

    @property
    def pretty_id(self):
        """Generate pretty_id value"""
        # Zezwalamy, aby order_id miał 10 znaków.
        if self.pretty_id_snap:
            return self.pretty_id_snap

        prefix = self.channel.order_pretty_id_prefix or self.channel.pk
        left = str(prefix).zfill(2)
        right_count = 10 - len(left)
        if self.channel.pretty_id_is_random_right_part:
            while True:
                timestamp_created = self.created.timestamp()
                salt = f"{self.in_channel_id}{timestamp_created}{settings.SECRET_KEY[:10]}"
                right = str(abs(hash(salt)) % (10**right_count)).zfill(right_count)
                if not Order.objects.filter(pretty_id_snap=f"{left}{right}").exists():
                    break
        else:
            right = str(self.in_channel_id).zfill(right_count)

        self.pretty_id_snap = f"{left}{right}"
        self.save(update_fields=["pretty_id_snap"])
        return self.pretty_id_snap

    @staticmethod
    def split_pretty_id(pretty_id: str):
        """Deprecated"""
        if len(pretty_id) != 10:
            raise Exception(f"It is not valid pretty_id={pretty_id}")
            # return None, None
        channel_pk = pretty_id[:2].lstrip("0")
        in_channel_id = pretty_id[2:].lstrip("0")
        return channel_pk, in_channel_id

    @classmethod
    def create(
        cls,
        data,
        cart: "Cart",
        channel: "Channel",
        request,
        customer=None,
        process_payment: bool = True,
        original_cart: Optional["Cart"] = None,
    ):
        """Checks if conditions are met,
        if yes, creates an Order inside a transaction,
        otherwise throws exception"""
        # check preconditions
        if cart is None:
            raise Exception("Unable to create order: cart must be not None")

        if cart.validation_status != ValidationStatus.VALID:
            raise Exception(f"Unable to create order for cart {cart.cart_id}: cart is not valid")

        if cart.cart_status in [CartStatus.CLOSED, CartStatus.CONFIRMED]:
            raise Exception(
                f"Unable to create order for cart {cart.cart_id}: cart is already closed or confirmed, status={cart.cart_status}"
            )

        # commit order to database
        else:
            with transaction.atomic():
                is_cash_on_delivery = cart.selected_payment_method.is_cash_on_delivery
                is_free_order = cart.selected_payment_method.is_free_order
                voucher_applied = Decimal(str(getattr(data.cart, "voucher_amount_applied", None) or 0))
                remaining_to_pay = Decimal(str(getattr(data, "total", None) or 0))
                fully_voucher_covered = voucher_applied > 0 and remaining_to_pay <= 0
                if is_cash_on_delivery or is_free_order:
                    order_status = OrderStatus.CONFIRMED
                else:
                    order_status = OrderStatus.UNPAID

                billing_email = None
                shipping_email = None
                if data.addresses:
                    if data.addresses.billing_address and hasattr(data.addresses.billing_address, "email"):
                        billing_email = data.addresses.billing_address.email
                    if data.addresses.shipping_address and hasattr(data.addresses.shipping_address, "email"):
                        shipping_email = data.addresses.shipping_address.email

                order = cls(
                    order_status=order_status,
                    order_body=sanitize(asdict(data)),
                    channel=channel,
                    customer=customer,
                    cart=cart,
                    billing_email=billing_email,
                    shipping_email=shipping_email,
                )
                skus = [item.sku for item in data.cart.items]
                products = list(Product.objects.filter(real_product__sku__in=skus, shop__idx=channel.idx))

                shipment_item = Shipping(
                    order=order,
                    code=data.shipping_method.code,
                    method=cart.selected_shipping_method,
                    total_price=data.shipping_method.total_price,
                )

                preorder_skus = (
                    skus_have_attribute(
                        channel_idx=channel.idx,
                        sku_list=skus,
                        feature_idx=channel.preorder_feature_idx,
                        filter_attr_value=channel.preorder_feature_value,
                    )
                    if channel.preorder_feature_idx and channel.preorder_feature_value
                    else []
                )

                preorder_date_skus = (
                    fetch_products_feature(
                        channel_idx=channel.idx, sku_list=skus, feature_idx=channel.preorder_date_feature_idx
                    )
                    if channel.preorder_date_feature_idx
                    else {}
                )

                preorder = False
                preorder_date = None

                items = []
                for item in data.cart.items:
                    preorder_item = False
                    preorder_item_date = None
                    if item.sku in preorder_skus:
                        preorder = True
                        preorder_item = True
                        preorder_date_pa = preorder_date_skus.get(item.sku, None)
                        if preorder_date_pa:
                            preorder_item_date = preorder_date_pa.get_value()
                            if preorder_date is None:
                                preorder_date = preorder_item_date
                            else:
                                preorder_date = max(preorder_date, preorder_item_date)

                    product_for_item = next((elem for elem in products if elem.sku == item.sku), None)

                    items.append(
                        Item(
                            order=order,
                            sku=item.sku,
                            quantity=item.quantity,
                            unit_price=item.unit_price,
                            total_price=item.total_price,
                            additional_info=item.extra,
                            product=product_for_item,
                            preorder=preorder_item,
                            preorder_date=preorder_item_date,
                        )
                    )
                order.preorder = preorder
                order.preorder_date = preorder_date

                order.order_body = sanitize(asdict(data))

                from django_checkout.domain.customs_threshold import ABOVE_THRESHOLD

                order.order_body["is_vat_0_modified_custom"] = (
                    getattr(getattr(data, "cart", None), "customs_threshold_scenario", None) == ABOVE_THRESHOLD
                )
                supplier = channel.channel_suppliers.filter(is_global=True).first()
                supplier_customers = (
                    customer.customer_suppliers.filter(supplier__channel=channel).first() if customer else None
                )
                supplier_customer = supplier_customers.supplier if supplier_customers else supplier

                reservations = []
                processed_bundle = []
                for item in data.cart.items:
                    if item.sub_items:
                        for sub_item in item.sub_items:
                            product = ProductRepresentation.objects.get(sku=sub_item.sku, channel=channel)
                            try:
                                stock = Stock.objects.get(product=product, supplier=supplier_customer)
                            except ObjectDoesNotExist:
                                stock = Stock.objects.get(product=product, supplier=supplier)
                            reservation = StockReservation(
                                stock=stock, order=order, reserved_quantity=sub_item.quantity
                            )
                            reservations.append(reservation)
                        processed_bundle.append(item.sku)

                for item in items:
                    if item.sku not in processed_bundle:
                        product = ProductRepresentation.objects.get(sku=item.sku, channel=channel)
                        try:
                            stock = Stock.objects.get(product=product, supplier=supplier_customer)
                        except ObjectDoesNotExist:
                            stock = Stock.objects.get(product=product, supplier=supplier)
                        reservation = StockReservation(stock=stock, order=order, reserved_quantity=item.quantity)
                        reservations.append(reservation)
                to_save = [order, shipment_item, *items, *reservations]

                payment_item = None
                voucher_intent = None
                if process_payment:
                    # Multi-method support: one PaymentIntent per payment method in cart
                    pms = data.payment_method or []
                    if not isinstance(pms, list):
                        pms = [pms]
                    seen_intent_codes = set()
                    for pm_data in pms:
                        if pm_data.code in seen_intent_codes:
                            continue
                        seen_intent_codes.add(pm_data.code)
                        method = PaymentMethod.objects.filter(channel=cart.channel, code=pm_data.code).first()
                        is_voucher = _is_voucher_method(method, pm_data.code)
                        if is_voucher and voucher_intent is not None:
                            continue
                        if not is_voucher and fully_voucher_covered:
                            continue
                        intent = PaymentIntent(
                            order=order,
                            code=pm_data.code,
                            method=method,
                            amount=_intent_amount(is_voucher, data),
                            currency=data.currency_code,
                        )
                        if is_voucher:
                            voucher_intent = intent
                        to_save.append(intent)
                        if payment_item is None:
                            payment_item = intent

                for elem in to_save:
                    elem.save()

                is_valid = order_created_signal.send(sender=Order.__class__, cart_id=cart.cart_id)
                if is_valid and not is_valid[0][1]:
                    raise Exception("Unable to create order: order_created_signal failed")

                cart.cart_status = CartStatus.CONFIRMED
                cart.save()

                # Process payment for EACH selected payment method (multi-method
                # support: voucher + payu, etc.). Voucher providers debit
                # balance immediately; gateway providers (payu, stripe) build a
                # redirect URL. Final redirect_url comes from the last provider
                # that produced one (typically the external gateway for the
                # remaining-to-pay amount; voucher provider returns None).
                redirect_url = None
                payment_error = None
                if process_payment:
                    selected_methods = cart.selected_payment_methods or [cart.selected_payment_method]
                    for method in selected_methods:
                        if method is None:
                            continue
                        if fully_voucher_covered and not _is_voucher_method(method, method.code):
                            continue
                        provider = method.get_provider()
                        provider.request = request
                        provider.process_order(order, original_cart)
                        if _is_voucher_method(method, method.code):
                            intent_for_method = voucher_intent or payment_item
                        else:
                            intent_for_method = next(
                                (i for i in to_save if isinstance(i, PaymentIntent) and i.code == method.code),
                                payment_item,
                            )
                        processed_amount = provider.get_processed_amount(order)
                        if (
                            processed_amount is not None
                            and intent_for_method is not None
                            and intent_for_method.amount != processed_amount
                        ):
                            intent_for_method.amount = processed_amount
                            intent_for_method.save(update_fields=["amount"])
                        provider.process_payment(
                            payment=intent_for_method,
                            is_cash_on_delivery=is_cash_on_delivery,
                            is_free_order=is_free_order,
                        )
                        provider_redirect = provider.get_redirection()
                        if provider_redirect:
                            redirect_url = provider_redirect
                        if provider.error:
                            payment_error = provider.error

                if process_payment and fully_voucher_covered and payment_error is None:
                    order.modify_status(OrderStatus.CONFIRMED)

                return order, redirect_url, payment_error

    def modify_status(self, status):
        is_diffs = True if self.order_status != status else False
        if is_diffs:
            self.order_status = status
            self.save()
        return is_diffs

    @property
    def as_data(self) -> OrderData:
        return OrderData.Schema(unknown=EXCLUDE).load(self.order_body, partial=True)

    @property
    def selected_shipping_method(self):
        data = self.as_data
        if (
            data.addresses is not None
            and data.shipping_method is not None
            and data.addresses.shipping_address is not None
        ):
            selected = ShippingOption.objects.get_item(
                data.shipping_method.code,
                data.addresses.shipping_address.country_code,
                data.currency_code,
                self.channel,
                [SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in data.cart.items],
                is_logged=True if self.customer else False,
            )
            return selected
        else:
            return None

    @property
    def selected_payment_method(self):
        """First payment method (legacy single-method accessor)."""
        methods = self.selected_payment_methods
        return methods[0] if methods else None

    @property
    def selected_payment_methods(self):
        """All payment methods attached to this order."""
        from django_checkout.models.payment_method import resolve_payment_methods_from_data

        return resolve_payment_methods_from_data(self.channel, self.as_data.payment_method)

    def anonymize(self) -> bool:
        # Operate directly on raw order_body to be resilient to schema drift
        # (e.g. lanks in old orders).
        body = self.order_body or {}
        # both must run — `or` would short-circuit the second call
        anonymized = anonymize_addresses_in_body(body)
        scrubbed = scrub_payment_secrets_in_body(body)
        save = anonymized or scrubbed
        if save:
            self.order_body = sanitize(body)
            self.save()
        return save

    def __str__(self):
        channel_name = self.channel.label if self.channel is not None else ""
        return f"{self.pk} | {channel_name} | {self.order_status}"
