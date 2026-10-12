# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Case, Q, Value, When
from django.db.models.fields import DecimalField
from django_accounts.models import Group

from django_checkout.compat import check_constraint
from django_checkout.domain.payment_provider import (
    AutopayPaymentProvider,
    BasePaymentProvider,
    PayNowPaymentProvider,
    PayPalPaymentProvider,
    PayUBlikPaymentProvider,
    PayUCardPaymentProvider,
    PayUPaymentProvider,
    Przelewy24PaymentProvider,
    VoucherPaymentProvider,
)
from django_checkout.domain.payment_provider.stripe_payment_provider import StripePaymentProvider
from django_checkout.enums import FeeType, PaymentProvider
from django_checkout.models.limited_products import LimitedProducts
from django_checkout.models.linked_products import LinkedProducts

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


class PaymentMethodQuerySet(models.QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)

    def filter_by_cash_on_delivery(self, cash_on_delivery_available, selected_shipping_method):
        if cash_on_delivery_available:
            cod_fee = selected_shipping_method.cash_on_delivery_fee
            return self.filter(is_free_order=False).annotate(
                cash_on_delivery_fee=Case(
                    When(Q(is_cash_on_delivery=True), then=Value(cod_fee)),
                    default=Value(0),
                    output_field=DecimalField(),
                )
            )
        else:
            return self.filter(is_cash_on_delivery=False).annotate(
                cash_on_delivery_fee=Value(0, output_field=DecimalField())
            )

    def get_only_pm_available_for_cart_products(self, sku_qty_list, channel, is_logged):
        """
        Returns ShippingOptions for all products in sku_list and channel.
        It includes ShippingMethods if all products are linked to it or if there's no intersection with other methods' linked products.
        """
        if not LinkedProducts.objects.filter(is_active=True).exists():
            return self

        all_payment_methods = PaymentMethod.objects.all().filter(channel=channel)
        all_available_pm = []
        sku_list = [item.sku for item in sku_qty_list] if sku_qty_list else []
        all_linked_products = {
            pm: LinkedProducts.objects.filter_products_by_linked(sku_list, None, pm, channel, is_logged)
            for pm in all_payment_methods
        }

        for payment_method in all_payment_methods:
            linked_products = all_linked_products[payment_method]
            other_methods_linked_products = set()
            for other_method in all_payment_methods:
                if other_method != payment_method:
                    other_methods_linked_products.update(all_linked_products[other_method])

            if set(sku_list).issubset(linked_products) or not set(sku_list).intersection(other_methods_linked_products):
                all_available_pm.append(payment_method.code)

        return self.filter(code__in=all_available_pm)

    def get_only_not_limited_pm(self, sku_list, channel, is_logged):
        """
        Exclude payment methods that are limited by LimitedProducts.
        """

        if not LimitedProducts.objects.filter(is_active=True).exists():
            return self

        all_payment_methods = PaymentMethod.objects.all().filter(channel=channel)
        all_available_pm = []
        for payment_method in all_payment_methods:
            available_products_by_pm, *_ = LimitedProducts.objects.filter_products_by_limitation(
                sku_list, None, payment_method, channel, is_logged
            )
            if all([sku in available_products_by_pm for sku in sku_list]):
                all_available_pm.append(payment_method.code)

        return self.filter(code__in=all_available_pm)

    def get_only_by_customer_group(self, customer_group):
        if customer_group:
            # Zwróć wszystkie metody płatności bez grupy (customer_group=None)
            # oraz te pasujące do podanej grupy
            return self.filter(Q(customer_group__isnull=True) | Q(customer_group=customer_group))
        else:
            # Zwróć tylko metody płatności bez przypisanej grupy
            return self.filter(customer_group__isnull=True)


class PaymentMethodManager(models.Manager):
    def get_queryset(self):
        return PaymentMethodQuerySet(self.model, using=self._db)


class PaymentMethod(models.Model):
    """table holding available payment methods"""

    channel: "Channel" = models.ForeignKey("Channel", on_delete=models.CASCADE)
    provider = models.CharField(max_length=24, choices=PaymentProvider.choices, default=PaymentProvider.TRANSFER)
    countries = models.ManyToManyField("django_regional.Country")
    currencies = models.ManyToManyField("django_regional.Currency")
    is_cash_on_delivery = models.BooleanField(editable=False)
    is_free_order = models.BooleanField(editable=False, default=False)
    code = models.CharField(max_length=20)
    image = models.ImageField(upload_to="pic", null=True, blank=True)
    name_t9n = models.JSONField(blank=True, null=True)
    description_t9n = models.JSONField(blank=True, null=True)
    additional_data = models.JSONField(blank=True, null=True)
    fee_type = models.CharField(max_length=24, choices=FeeType.choices, default=FeeType.WITHOUT_FEE)
    fee_value = models.FloatField(
        validators=[MinValueValidator(0.0)],
        default=0.0,
        help_text="Provide a percentage fee (e.g. 20.00) or flat fee value",
    )
    position = models.PositiveIntegerField(default=0, blank=False, null=False)
    customer_group: "Group" = models.ForeignKey(
        "django_accounts.Group",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="Group that can use this payment method. Leave blank to allow all users.",
    )
    objects = PaymentMethodManager()

    def __str__(self):
        return f"[{self.channel.idx}] {self.code}"

    def save(self, *args, **kwargs):
        if self.provider == PaymentProvider.COD:
            self.is_cash_on_delivery = True
        else:
            self.is_cash_on_delivery = False
        if self.provider == PaymentProvider.FREE_ORDER:
            self.is_free_order = True
        else:
            self.is_free_order = False
        super().save(*args, **kwargs)

    def _get_provider_cls(self):
        # When more providers, make IF
        if self.provider == PaymentProvider.PAYU:
            return PayUPaymentProvider
        if self.provider == PaymentProvider.PAYU_CARD:
            return PayUCardPaymentProvider
        if self.provider == PaymentProvider.PAYPAL:
            return PayPalPaymentProvider
        if self.provider == PaymentProvider.AUTOPAY:
            return AutopayPaymentProvider
        if self.provider == PaymentProvider.PRZELEWY24:
            return Przelewy24PaymentProvider
        if self.provider == PaymentProvider.PAYNOW:
            return PayNowPaymentProvider
        if self.provider == PaymentProvider.STRIPE:
            return StripePaymentProvider
        if self.provider == PaymentProvider.PAYU_BLIK:
            return PayUBlikPaymentProvider
        if self.provider == PaymentProvider.VOUCHER:
            if VoucherPaymentProvider is None:
                from django.core.exceptions import ImproperlyConfigured

                raise ImproperlyConfigured(
                    "PaymentMethod.provider='voucher' requires django-checkout-voucher to be installed."
                )
            return VoucherPaymentProvider
        return BasePaymentProvider

    def _get_provider_init_args(self):
        args = [self]
        kwargs = {}
        return args, kwargs

    def get_provider(self):
        args, kwargs = self._get_provider_init_args()
        cls = self._get_provider_cls()
        return cls(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "code"], name="unique_payment_method_code_per_channel"),
            models.UniqueConstraint(
                fields=["channel"],
                name="unique_cod_payment_per_channel",
                condition=(Q(is_cash_on_delivery=True) & Q(provider=PaymentProvider.COD)),
            ),
            check_constraint(
                condition=(
                    (Q(is_cash_on_delivery=True) & Q(provider=PaymentProvider.COD)) | Q(is_cash_on_delivery=False)
                ),
                name="provider_cod_or_is_cash_on_delivery_false",
            ),
            models.UniqueConstraint(
                fields=["channel"],
                name="unique_free_order_payment_per_channel",
                condition=(Q(is_free_order=True) & Q(provider=PaymentProvider.FREE_ORDER)),
            ),
            check_constraint(
                condition=((Q(is_free_order=True) & Q(provider=PaymentProvider.FREE_ORDER)) | Q(is_free_order=False)),
                name="provider_free_order_or_is_free_order_false",
            ),
        ]


def resolve_payment_methods_from_data(channel, payment_method_data):
    """Return PaymentMethod model rows matching list of PaymentData entries (by channel + code).

    Shared by Cart.selected_payment_methods and Order.selected_payment_methods —
    avoids duplicating multi-method resolution logic on both models.

    Single bulk query (one round-trip) regardless of how many payment methods
    are listed. Preserves the input order (matters for primary-method dispatch
    in Order.create) and drops codes that don't resolve to a row.
    """
    if not payment_method_data:
        return []
    codes = [pm.code for pm in payment_method_data if getattr(pm, "code", None)]
    if not codes:
        return []
    pms_by_code = {pm.code: pm for pm in PaymentMethod.objects.filter(channel=channel, code__in=codes)}
    return [pms_by_code[pm.code] for pm in payment_method_data if pm.code in pms_by_code]
