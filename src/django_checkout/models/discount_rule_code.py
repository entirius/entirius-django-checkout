# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import unique
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import TextChoices
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    pass


@unique
class TargetForDiscountRule(TextChoices):
    ALL = "all", _("Discount for everyone.")
    FIRST_ORDER_LOGGED = "first_order_logged", _("Discount only available for logged-in users without prior orders.")
    FIRST_ORDER_ALL = "first_order_all", _("Discount for all users without prior orders.")


@unique
class ModifiersForDiscountRule(TextChoices):
    NONE = "None", _("Without discount")
    PERCENT_DISCOUNT = "percent_discount", _("Percent discount.")
    PRICE_DISCOUNT = "price_discount", _("Price discount.")
    STEP_QTY_PERCENT_DISCOUNT = (
        "step_qty_percent_discount",
        _("Progressive/step percent discount based on quantity of the product (line)."),
    )
    STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART = (
        "step_qty_percent_discount_whole_cart",
        _("Progressive/step percent discount based on the sum of the quantity whole cart."),
    )
    STEP_QTY_PRICE_DISCOUNT_WHOLE_CART = (
        "step_qty_price_discount_whole_cart",
        _("Progressive/step price discount based on the sum of the quantity whole cart."),
    )
    STEP_QTY_FIXED_PRICE_PER_CURRENCY = (
        "step_qty_fixed_price_per_currency",
        _("Progressive/step fixed price per currency based on quantity in cart."),
    )
    STEP_PRICE_PERCENT_DISCOUNT = (
        "step_price_percent_discount",
        _("Progressive/step price discount based on the total amount whole cart."),
    )
    CHEAPEST_GRATIS = "cheapest_gratis", _("The role defining how many units of the cheapest product should be free.")
    MOST_EXPENSIVE_GRATIS = (
        "most_expensive_gratis",
        _("The role defining how many units of the most expensive product should be free."),
    )
    GRATIS_STEPPED = (
        "gratis_stepped",
        _(
            "The role defining how many units of the gratis product should be gratis. (all product in ModeOfAction are available for gratis, will be picked by client in cart)"
        ),
    )
    GRATIS_BY_SKU_IN_CART = (
        "gratis_by_sku_in_cart",
        _(
            "The role allowing gratis, but only when all sku in extra_value are in cart (all product in ModeOfAction are available for gratis, will be picked by client in cart)"
        ),
    )


class DiscountRuleCode(models.Model):
    channels = models.ManyToManyField("Channel", related_name="discount_rule_code_channels")
    name = models.CharField(blank=True, null=True, max_length=128)
    free_shipping = models.BooleanField(default=False, help_text="Czy rola koszykowa ma powodować darmową dostawę.")
    free_shipping_methods = models.ManyToManyField(
        "ShippingMethod", help_text="Metody dostawy kwalifikujące się na darmową dostawę.", blank=True
    )
    min_order_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default="0.0",
        help_text="Minimalna wartość koszyka, dla którego zadziała rola koszykowa",
    )
    free_order = models.BooleanField(default=False, help_text="Czy rola koszykowa ma powodować darmowe zamówienie.")
    is_omnibus = models.BooleanField(
        default=False,
        help_text="Flaga oznacza, że reguła jest publiczna na takim poziomie, który kwalifikuje ją do przeliczeń ceny omnibus.",
    )
    target = models.CharField(max_length=256, default=TargetForDiscountRule.ALL, choices=TargetForDiscountRule.choices)
    modifier = models.CharField(
        max_length=256, default=ModifiersForDiscountRule.NONE, choices=ModifiersForDiscountRule.choices
    )
    extra_value = models.JSONField(default=dict)
    extension = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dodatkowe dane w formacie JSON (np. custom copy, opisy, teksty marketingowe).",
    )
    priority = models.PositiveSmallIntegerField(blank=True, null=True)
    combine_with_other_rules = models.BooleanField(default=False)
    automatic_applications = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, help_text="Czy reguła rabatowa jest aktywna.")
    currencies = models.ManyToManyField(
        "django_regional.Currency",
        blank=True,
        related_name="discount_rule_code_currency",
        help_text="Waluty, dla których reguła rabatowa jest ważna. Brak oznacza dla wszystkich.",
    )  # Waluty aktualnie tylko do obsługi gratisów
    show_when_invalid = models.BooleanField(
        default=True,
        help_text="Czy reguła gratisowa ma być widoczna w /gratis-rules/ nawet gdy użytkownik nie spełnia warunków.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    objects = models.Manager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    def __str__(self):
        match self.modifier:
            case ModifiersForDiscountRule.PRICE_DISCOUNT:
                info = self.extra_value
            case ModifiersForDiscountRule.PERCENT_DISCOUNT:
                info = f"{self.extra_value} %"
            case _:
                if self.free_shipping:
                    info = "free shipment"
                else:
                    info = "Other discount"
        return f"| {self.name} | {info} | {','.join(self.channels.values_list('idx', flat=True))} |"

    class Meta:
        verbose_name = "Discount Rule: Code"
        verbose_name_plural = "Discount Rules: Code"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.PERCENT_DISCOUNT) | models.Q(extra_value__gt=0),
                name="check_extra_value_positive_int_percent_discount",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.PRICE_DISCOUNT) | models.Q(extra_value__gt=0),
                name="check_extra_value_positive_int_price_discount",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT)
                | models.Q(extra_value__isnull=False),
                name="check_extra_value_not_empty_dict_step_qty_percent_discount",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.STEP_PRICE_PERCENT_DISCOUNT)
                | models.Q(extra_value__isnull=False),
                name="check_extra_value_not_empty_dict_step_price_percent_discount",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY)
                | models.Q(extra_value__isnull=False),
                name="check_extra_value_not_empty_dict_step_qty_fixed_price_per_currency",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.CHEAPEST_GRATIS) | models.Q(extra_value__gt=0),
                name="check_extra_value_positive_int_cheapest_gratis",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS)
                | models.Q(extra_value__gt=0),
                name="check_extra_value_positive_int_most_expensive",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.GRATIS_STEPPED)
                | models.Q(extra_value__isnull=False),
                name="check_extra_value_not_empty_dict_gratis_stepped",
            ),
            models.CheckConstraint(
                condition=models.Q(modifier=ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART)
                | models.Q(extra_value__isnull=False),
                name="check_extra_value_not_empty_list_gratis_by_sku_in_cart",
            ),
        ]
        ordering = ["-id"]
