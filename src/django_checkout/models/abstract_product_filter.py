# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import unique

from django.db import models
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _

from django_checkout.compat import check_constraint


@unique
class FilterModeType(TextChoices):
    INCLUSION = "inclusion", _("Includes all products with specific parameters.")
    EXCLUSION = "exclusion", _("Excludes all products with specific parameters.")


@unique
class PriceRestrictionType(TextChoices):
    BRUTTO = "brutto", _("Brutto")
    NETTO = "netto", _("Netto")


class AbstractProductFilter(models.Model):
    """
    Abstract base model for filtering products based on various criteria.
    Used by both GratisProductFilter and DiscountModeOfAction.
    """

    attributes = models.ManyToManyField("django_pim.Attribute", blank=True, related_name="%(class)s_attributes")
    features_qty_greater_than_attr_value = models.ManyToManyField(
        "django_pim.Feature",
        blank=True,
        related_name="%(class)s_features_qty_greater_than_attr_value",
        help_text="Include or exclude product which have product attribute associated with the given feature and if the quantity of the product in the cart exceeds the value of this attribute.",
    )
    features_qty_is_multiple_of_attr_value = models.ManyToManyField(
        "django_pim.Feature",
        blank=True,
        related_name="%(class)s_features_qty_is_multiple_of_attr_value",
        help_text="Include or exclude product which have product attribute associated with the given feature and if the quantity of the product in the cart is a multiple of the value of this attribute.",
    )
    categories = models.ManyToManyField("django_pim.ProductCategory", blank=True, related_name="%(class)s_categories")
    products = models.ManyToManyField(
        "django_pim.Product",
        blank=True,
        related_name="%(class)s_products",
        help_text="If you pick product configurable all child will be excluded/included",
    )
    take_common_part = models.BooleanField(default=False)
    product_price_to = models.PositiveIntegerField(blank=True, null=True)
    product_price_from = models.PositiveIntegerField(blank=True, null=True)
    cart_price_to = models.PositiveIntegerField(blank=True, null=True)
    cart_price_from = models.PositiveIntegerField(blank=True, null=True)
    qty_to = models.PositiveIntegerField(blank=True, null=True)
    qty_from = models.PositiveIntegerField(blank=True, null=True)
    cart_qty_to = models.PositiveIntegerField(blank=True, null=True)
    cart_qty_from = models.PositiveIntegerField(blank=True, null=True)
    is_inclusion_or_exclusion = models.CharField(
        max_length=256, default=FilterModeType.INCLUSION, choices=FilterModeType.choices
    )
    objects = models.Manager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        abstract = True
        constraints = [
            check_constraint(
                condition=models.Q(cart_price_to__gte=models.F("cart_price_from"))
                | models.Q(cart_price_to__isnull=True)
                | models.Q(cart_price_from__isnull=True),
                name="%(class)s_check_cart_price_to_gte_cart_price_from",
            ),
            check_constraint(
                condition=models.Q(qty_to__gte=models.F("qty_from"))
                | models.Q(qty_to__isnull=True)
                | models.Q(qty_from__isnull=True),
                name="%(class)s_check_qty_to_gte_qty_from",
            ),
            check_constraint(
                condition=models.Q(product_price_to__gte=models.F("product_price_from"))
                | models.Q(product_price_to__isnull=True)
                | models.Q(product_price_from__isnull=True),
                name="%(class)s_check_product_price_to_gte_product_price_from",
            ),
            check_constraint(
                condition=models.Q(cart_qty_to__gte=models.F("cart_qty_from"))
                | models.Q(cart_qty_to__isnull=True)
                | models.Q(cart_qty_from__isnull=True),
                name="%(class)s_check_cart_qty_to_gte_cart_qty_from",
            ),
        ]
        ordering = ["-id"]
