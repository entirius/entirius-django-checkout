# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, DecimalField, F, OuterRef, Q, Subquery, Value, When
from django_pim.models import Product

from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.shipping import get_delivery_matrix_prices
from django_checkout.enums import PriceType
from django_checkout.models.limited_products import LimitedProducts

# QuerySet
from django_checkout.models.linked_products import LinkedProducts
from django_checkout.models.shipping_method import ShippingMethod
from django_checkout.settings import SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES
from django_checkout.worker.product_virtual import check_all_virtual_products

if TYPE_CHECKING:
    from django_regional.models import Country, Currency


class ShippingOptionQuerySet(models.QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)

    def filter_by_currency(self, currency_iso3: str):
        available_for_currency = Q(currency__iso3=currency_iso3)
        return self.filter(available_for_currency)

    def filter_by_channel(self, channel):
        available_for_channel = Q(method__channel=channel)
        return self.filter(available_for_channel)

    def filter_by_country(self, country_iso2: str):
        available_for_country = Q(country_code=country_iso2) | Q(country_code="ALL")
        return self.filter(available_for_country)

    def _cart_product_ids(self, sku_list):
        return list(Product.objects.filter(real_product__sku__in=sku_list).values_list("id", flat=True))

    def get_only_sm_available_for_cart_products(self, sku_list, channel, is_logged):
        """
        Returns ShippingOptions for all products in sku_list and channel.
        It includes ShippingMethods if all products are linked to it or if there's no intersection with other methods' linked products.
        """

        if not LinkedProducts.objects.filter(is_active=True).exists():
            return self

        all_shipping_methods = list(ShippingMethod.objects.filter(channel=channel))
        cart_product_ids = self._cart_product_ids(sku_list)
        all_available_sm = []

        all_linked_products = {
            sm: LinkedProducts.objects.filter_products_by_linked(
                sku_list, sm, None, channel, is_logged, cart_product_ids
            )
            for sm in all_shipping_methods
        }

        for shipping_method in all_shipping_methods:
            linked_products = all_linked_products[shipping_method]

            other_methods_linked_products = set()
            for other_method in all_shipping_methods:
                if other_method != shipping_method:
                    other_methods_linked_products.update(all_linked_products[other_method])

            if set(sku_list).issubset(linked_products) or not set(sku_list).intersection(other_methods_linked_products):
                all_available_sm.append(shipping_method.code)
        return self.filter(method__code__in=all_available_sm)

    def get_only_not_limited_sm(self, sku_list, channel, is_logged):
        """Exclude ShippingOptions that are limited by LimitedProducts.

        Bulk implementation: three queries (LimitedProducts + Product +
        ProductAttribute) plus in-memory evaluation. Previous version ran
        the same filter pipeline once per shipping method (5-8 queries x N
        methods).
        """
        if not LimitedProducts.objects.filter(is_active=True).exists():
            return self

        all_shipping_methods = list(ShippingMethod.objects.filter(channel=channel))
        if not all_shipping_methods:
            return self.none()

        allowed_by_sm = LimitedProducts.objects.bulk_allowed_skus_per_sm(
            sku_list, channel, all_shipping_methods, is_logged
        )
        sku_set = set(sku_list)
        available_codes = [sm.code for sm in all_shipping_methods if sku_set <= allowed_by_sm.get(sm.pk, set())]
        return self.filter(method__code__in=available_codes)

    def get_only_one_sm_per_method_type_when_deduplication_on(self):
        """
        This method returns a QuerySet of ShippingOption objects that are associated with a ShippingMethod.
        For each method type listed in SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES, it includes only the ShippingMethod with the lowest priority value.
        It also includes all ShippingMethods whose method type is not listed in SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES.
        """

        subquery = ShippingMethod.objects.filter(
            method_type__in=SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES,
            method_type=OuterRef("method_type"),
            code__in=self.values_list("method__code", flat=True),
        ).order_by("priority")
        shipping_methods = ShippingMethod.objects.annotate(
            lowest_priority=Subquery(subquery.values("priority")[:1])
        ).filter(priority=F("lowest_priority"))
        shipping_methods_without_deduplication = ShippingMethod.objects.exclude(
            method_type__in=SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES
        )

        return self.filter(Q(method__in=shipping_methods) | Q(method__in=shipping_methods_without_deduplication))

    def filter_by_max_volume(self, sku_qty_list, channel):
        if not sku_qty_list:
            return self

        from django_checkout.domain.cart_limiters import check_items_volume

        total_volume, _missing, _by_sku = check_items_volume(sku_qty_list, channel)
        if not total_volume:
            return self

        return self.exclude(method__max_volume__isnull=False, method__max_volume__lt=total_volume)

    def get_only_sm_available_for_virtual_products(self, sku_list, channel, currency):
        """
        Returns ShippingOptions for all products in sku_list and channel.
        If all products are virtual, it returns only ShippingOptions for virtual products.
        If there are any physical products, it returns only ShippingOptions for physical products.
        """
        is_all_virtual_product = check_all_virtual_products(sku_list, channel, currency)

        if is_all_virtual_product:
            return self.filter(Q(method__method_type=ShippingMethod.MethodType.VIRTUAL))
        else:
            return self.filter(~Q(method__method_type=ShippingMethod.MethodType.VIRTUAL))

    def annotate_matrix_prices(self, sku_list, channel):
        if not ShippingMethod.objects.filter(channel=channel, price_type=PriceType.MATRIX).exists():
            return self.annotate(
                matrix_price_brutto=F("price_brutto"),
                matrix_price_brutto_modifier=Value(Decimal("0.00"), output_field=DecimalField()),
            )
        options = list(self.select_related("method"))
        cases_price_brutto = []
        cases_price_brutto_modifier = []
        for shipping_option in options:
            (
                _free_delivery_items,
                _free_delivery_items_modifier,
                _free_delivery_above_price,
                _free_delivery_above_price_modifier,
                new_shipping_price,
                new_shipping_price_modifier,
            ) = get_delivery_matrix_prices(shipping_option, sku_list, channel)

            cases_price_brutto.append(
                When(pk=shipping_option.pk, then=Value(new_shipping_price, output_field=DecimalField()))
            )
            cases_price_brutto_modifier.append(
                When(pk=shipping_option.pk, then=Value(new_shipping_price_modifier, output_field=DecimalField()))
            )

        return self.annotate(
            matrix_price_brutto=Case(
                *cases_price_brutto,
                default=Value(Decimal(0.00), output_field=DecimalField()),
                output_field=DecimalField(),
            ),
            matrix_price_brutto_modifier=Case(
                *cases_price_brutto_modifier,
                default=Value(Decimal(0.00), output_field=DecimalField()),
                output_field=DecimalField(),
            ),
        )


class ShippingOptionManager(models.Manager):
    def get_queryset(self):
        return ShippingOptionQuerySet(self.model, using=self._db)

    def get_available_items(
        self,
        country_iso2: str,
        currency_iso3: str,
        channel,
        sku_qty_list: list[SkuQuantityData] = None,
        is_logged=False,
    ):
        sku_list = [item.sku for item in sku_qty_list] if sku_qty_list else []
        return (
            self.all()
            .filter_by_country(country_iso2)
            .filter_by_currency(currency_iso3)
            .filter_by_channel(channel)
            .get_only_sm_available_for_cart_products(sku_list, channel, is_logged)
            .get_only_not_limited_sm(sku_list, channel, is_logged)
            .get_only_one_sm_per_method_type_when_deduplication_on()
            .filter_by_max_volume(sku_qty_list, channel)
            .get_only_sm_available_for_virtual_products(sku_list, channel, currency_iso3)
            .annotate_matrix_prices(sku_qty_list, channel)
        )

    def get_item(
        self,
        method_code: str,
        country_iso2: str,
        currency_iso3: str,
        channel,
        sku_qty_list: list[SkuQuantityData],
        is_logged,
    ):
        sku_list = [item.sku for item in sku_qty_list] if sku_qty_list else []
        return (
            self.all()
            .filter_by_country(country_iso2)
            .filter_by_currency(currency_iso3)
            .filter_by_channel(channel)
            .filter(method__code=method_code)
            .get_only_sm_available_for_cart_products(sku_list, channel, is_logged)
            .get_only_not_limited_sm(sku_list, channel, is_logged)
            .get_only_one_sm_per_method_type_when_deduplication_on()
            .filter_by_max_volume(sku_qty_list, channel)
            .get_only_sm_available_for_virtual_products(sku_list, channel, currency_iso3)
            .annotate_matrix_prices(sku_qty_list, channel)
            .first()
        )


class ShippingOption(models.Model):
    """
    table holding prices and rules for shipping methods per country
    """

    objects = ShippingOptionManager()
    method: "ShippingMethod" = models.ForeignKey("ShippingMethod", on_delete=models.CASCADE)
    country: "Country" = models.ForeignKey("django_regional.Country", null=True, blank=True, on_delete=models.CASCADE)
    country_code = models.CharField(max_length=10, default="ALL")
    currency: "Currency" = models.ForeignKey("django_regional.Currency", on_delete=models.CASCADE)
    tax_rate = models.DecimalField(max_digits=10, decimal_places=4, default=0.000)
    price_brutto = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    free_delivery_above_brutto = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    cash_on_delivery_available = models.BooleanField(default=False)
    cash_on_delivery_fee = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, default=None)

    def __str__(self):
        return f"{self.method} {self.country_code} {self.currency}"

    def save(self, **kwargs):

        cache_key_pattern = f"shipping_option:{self.pk}|*"
        cache.delete_pattern(cache_key_pattern)

        if self.country is not None:
            self.country_code = self.country.iso2
        elif self.country_code is None and self.country is None:
            self.country_code = "ALL"
        super().save()

    def clean(self):
        if (self.cash_on_delivery_available == False and self.cash_on_delivery_fee is not None) or (
            self.cash_on_delivery_available == True and self.cash_on_delivery_fee is None
        ):
            raise ValidationError("COD available and fee must both full or both empty")

    @property
    def channel(self):
        return self.method.channel

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["method", "country_code", "currency"], name="one_currency_per_country_per_method"
            ),
            models.CheckConstraint(
                condition=(
                    Q(country_code="FAKE", country__isnull=True)  # Fake deprecated - make migrations in major version
                    | Q(country_code="ALL", country__isnull=True)
                    | Q(country__isnull=False)
                ),
                name="has_country_or_avaliable_for_all",
            ),
            models.CheckConstraint(
                condition=(
                    (Q(cash_on_delivery_available=False) & Q(cash_on_delivery_fee__isnull=True))
                    | (Q(cash_on_delivery_available=True) & Q(cash_on_delivery_fee__isnull=False))
                ),
                name="cod_available_or_cod_extra_isnull",
            ),
        ]
