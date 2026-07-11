# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import unique
from typing import TYPE_CHECKING

from django.db import models
from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:
    from django_accounts.models import Customers, Group

    from django_checkout.models.discount_rule_code import DiscountRuleCode


@unique
class DiscountModeOfActionType(TextChoices):
    INCLUSION = "inclusion", _("Includes all products with specific parameters.")
    EXCLUSION = "exclusion", _("Excludes all products with specific parameters.")


class DiscountCustomerModeOfAction(models.Model):
    rule: "DiscountRuleCode" = models.ForeignKey(
        "DiscountRuleCode", related_name="discount_customer_mode_of_actions_rule", on_delete=models.CASCADE
    )
    customers: "Customers" = models.ManyToManyField(
        "django_accounts.Customer", blank=True, related_name="discount_mode_of_actions_customers", default=None
    )

    groups: "Group" = models.ManyToManyField(
        "django_accounts.Group", blank=True, related_name="discount_mode_of_actions_groups", default=None
    )
    take_common_part = models.BooleanField(default=False)
    is_inclusion_or_exclusion = models.CharField(
        max_length=256, default=DiscountModeOfActionType.INCLUSION, choices=DiscountModeOfActionType.choices
    )
    objects = models.Manager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-id"]
