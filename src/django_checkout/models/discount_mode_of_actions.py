# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

from django_checkout.models.abstract_product_filter import AbstractProductFilter, FilterModeType

if TYPE_CHECKING:
    from django_checkout.models.discount_rule_code import DiscountRuleCode


DiscountModeOfActionType = FilterModeType


# OLD MODEL - kept for backward compatibility, table will be renamed in migration
class DiscountModeOfAction(AbstractProductFilter):
    """
    Filter that defines which products can be selected as gratis (free products).
    Also used for percent/price discounts to filter which products receive the discount.

    NOTE: This model's table will be renamed to 'gratisproductfilter' in migrations.
    Use GratisProductFilter for new code.
    """

    rule: "DiscountRuleCode" = models.ForeignKey(
        "DiscountRuleCode", related_name="discount_mode_of_actions_rule", on_delete=models.CASCADE
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-id"]


# NEW MODEL - for filtering products that count towards gratis threshold
class ThresholdProductFilter(AbstractProductFilter):
    """
    Filter that defines which products from the cart should be counted
    towards the gratis threshold calculation.

    For example: if only products from category X should count towards
    the 200 PLN threshold for gratis eligibility.
    """

    rule: "DiscountRuleCode" = models.ForeignKey(
        "DiscountRuleCode", related_name="threshold_filters", on_delete=models.CASCADE
    )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-id"]
