# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import unique
from typing import TYPE_CHECKING

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from idx_normalizator import normalize_idx, validate_idx

from django_checkout.enums import SplitOrderPaymentFeeMechanism, SplitOrderShippingCostMechanism

if TYPE_CHECKING:
    from django_regional.models import Country, Currency, Language


@unique
class DiscountApplyType(TextChoices):
    BEFORE = "before", _("Add discount before tax applied. Discount from price net.")
    AFTER = "after", _("Add discount after tax applied. Discount from price gross.")


@unique
class PriceRestrictionType(TextChoices):
    BRUTTO = "brutto", _("Brutto")
    NETTO = "netto", _("Netto")


@unique
class SplitOrderMechanism(TextChoices):
    MODIFY_AND_CREATE = "MODIFY_AND_CREATE", _("Modify original order and create new needed orders")


class Channel(models.Model):
    idx = models.CharField(max_length=128, blank=False, null=False, unique=True)
    label = models.CharField(max_length=128, blank=False, null=False, default="", unique=True)
    default_language: "Language" = models.ForeignKey(
        "django_regional.Language",
        related_name="default_channels",
        verbose_name="default_language",
        help_text="",
        null=False,
        blank=False,
        on_delete=models.PROTECT,
    )
    default_currency: "Currency" = models.ForeignKey(
        "django_regional.Currency",
        related_name="default_channels",
        verbose_name="default_currency",
        help_text="",
        null=False,
        blank=False,
        on_delete=models.PROTECT,
    )
    default_country: "Country" = models.ForeignKey(
        "django_regional.Country",
        related_name="default_channels",
        verbose_name="default_country",
        help_text="",
        null=False,
        blank=False,
        on_delete=models.PROTECT,
    )
    discount_mode_of_action_price_restriction_type = models.CharField(
        choices=PriceRestrictionType.choices,
        max_length=256,
        default=PriceRestrictionType.BRUTTO,
        help_text="Declare if the price restriction for discount mode of action is netto or brutto",
    )

    languages = models.ManyToManyField(
        "django_regional.Language", related_name="channels", verbose_name="languages", blank=True
    )
    currencies = models.ManyToManyField(
        "django_regional.Currency", related_name="channels", verbose_name="currencies", blank=True
    )
    countries = models.ManyToManyField(
        "django_regional.Country", related_name="channels", verbose_name="countries", blank=True
    )
    order_pretty_id_prefix = models.CharField(max_length=2, blank=True, null=True)

    pretty_id_is_random_right_part = models.BooleanField(
        default=False,
        help_text="If True, the right part of the pretty ID will be random. If False, it will be sequential.",
    )

    # Split order
    feature_idxs_for_split_order = models.CharField(max_length=256, blank=True)
    default_attr_split = models.CharField(
        max_length=256,
        blank=True,
        help_text="Default attribute idx for split order, if this field = None - product without attribute will be split to separate order",
    )
    force_splitting_order_in_checkout = models.BooleanField(
        default=False,
        help_text="Force splitting order in checkout. If False order will be split only if it will be declared in cart by customer.",
    )
    split_order_shipping_cost_mechanism = models.CharField(
        max_length=1024,
        default=SplitOrderShippingCostMechanism.ATTRIBUTE_BASED_SHIPPING_COST_ASSIGNMENT,
        choices=SplitOrderShippingCostMechanism.choices,
        help_text="Mechanism for split order shipping cost assignment",
    )
    default_attr_split_shipping = models.CharField(
        max_length=256, blank=True, help_text="Assign a shipping cost to an order with this attribute"
    )
    split_order_payment_fee_mechanism = models.CharField(
        max_length=1024,
        default=SplitOrderPaymentFeeMechanism.ATTRIBUTE_BASED_PAYMENT_FEE_ASSIGNMENT,
        choices=SplitOrderPaymentFeeMechanism.choices,
        help_text="Mechanism for split order payment_fee assignment",
    )
    default_attr_split_payment_fee = models.CharField(
        max_length=256, blank=True, help_text="Assign a payment fee to an order with this attribute"
    )
    min_order_price = models.PositiveIntegerField(validators=[MinValueValidator(0)], blank=True, null=True, default=0)
    discount_apply_type = models.CharField(
        max_length=10, default=DiscountApplyType.AFTER, choices=DiscountApplyType.choices
    )
    split_order_mechanism = models.CharField(
        max_length=45, default=SplitOrderMechanism.MODIFY_AND_CREATE, choices=SplitOrderMechanism.choices
    )
    feature_separate_to_other_order = models.JSONField(
        default=dict,
        blank=True,
        help_text="Declare which features should be separated to other order. Example: ['feature1', 'feature2']",
    )

    # Preorder
    preorder_feature_idx = models.CharField(max_length=256, blank=True)
    preorder_feature_value = models.CharField(max_length=256, blank=True)
    preorder_date_feature_idx = models.CharField(max_length=256, blank=True)

    show_product_prices_when_invalid = models.BooleanField(
        default=False,
        help_text="Show product prices when they are invalid. If False, invalid prices will be hidden.",
    )
    objects = models.Manager()

    @staticmethod
    def normalize_idx(idx):
        """Deprecated"""
        return normalize_idx(idx)

    @staticmethod
    def validate_idx(idx):
        """Deprecated"""
        validate_idx(idx)

    def save(self, *args, **kwargs):
        Channel.validate_idx(self.idx)
        super().save(*args, **kwargs)
        if self.default_language is not None:
            self.languages.add(self.default_language)
        if self.default_currency is not None:
            self.currencies.add(self.default_currency)
        if self.default_country is not None:
            self.countries.add(self.default_country)

    def __str__(self):
        return f"{self.label} [{self.idx}]"

    def is_discount_after_tax(self):
        return self.discount_apply_type == DiscountApplyType.AFTER

    def is_discount_before_tax(self):
        return self.discount_apply_type == DiscountApplyType.BEFORE

    class Meta:
        ordering = []
        verbose_name_plural = "channels"
