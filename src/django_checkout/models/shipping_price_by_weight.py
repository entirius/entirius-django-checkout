# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db import models

from django_checkout.models.managers.shipping_price_matrix import ShippingPriceByWeightManager

if TYPE_CHECKING:
    from django_checkout.models.shipping_option import ShippingOption


class ShippingPriceMatrix(models.Model):
    class FilterType(models.TextChoices):
        ALL_REST = "all", "ALL"
        ATTR = "attr", "ATTR"

    shipping_option: "ShippingOption" = models.ForeignKey(
        "ShippingOption", related_name="shipping_price_matrixes", on_delete=models.CASCADE
    )
    filter_type = models.CharField(max_length=24, choices=FilterType.choices, default=FilterType.ALL_REST)
    free_delivery_above_brutto = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    idx = models.CharField(max_length=256, blank=True, null=True)
    value = models.CharField(max_length=256, blank=True, null=True)
    objects = models.Manager()

    def save(self, **kwargs):
        cache_key_pattern = f"shipping_option:{self.pk}|*"
        cache.delete_pattern(cache_key_pattern)
        super().save(**kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["shipping_option", "filter_type", "idx", "value"], name="unique_shipping_price_matrix"
            )
        ]
        ordering = ["shipping_option", "filter_type"]
        verbose_name = "Shipping Price Matrix"
        verbose_name_plural = "Shipping Price Matrixes"


class ShippingPriceByWeight(models.Model):
    shipping_price_matrix: "ShippingPriceMatrix" = models.ForeignKey(
        "ShippingPriceMatrix",
        related_name="shipping_price_option_matrix",
        on_delete=models.CASCADE,
        null=False,
        blank=False,
    )
    weight = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    price_brutto = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    objects = ShippingPriceByWeightManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["shipping_price_matrix", "weight"], name="unique_shipping_price_by_weight")
        ]
        verbose_name = "Shipping Price By Weight"
        verbose_name_plural = "Shipping Prices By Weight"
