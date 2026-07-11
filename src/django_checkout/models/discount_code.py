# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models
from django.db.models import F, Q

if TYPE_CHECKING:
    from django_checkout.models.discount_rule_code import DiscountRuleCode


class DiscountCode(models.Model):
    rule: "DiscountRuleCode" = models.ForeignKey("DiscountRuleCode", related_name="codes", on_delete=models.CASCADE)
    code = models.CharField(max_length=40)
    max_used = models.IntegerField(default=1)
    max_uses_per_user = models.IntegerField(default=None, blank=True, null=True)
    current_used = models.IntegerField(default=0)
    active_from = models.DateField(blank=True, null=True, default=None)
    active_to = models.DateField(blank=True, null=True, default=None)
    max_products_qty = models.IntegerField(blank=True, null=True)
    objects = models.Manager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(current_used__lte=F("max_used")), name="current_used_lesser_than_or_equal_to_max_used"
            ),
            models.CheckConstraint(
                condition=models.Q(active_to__gte=models.F("active_from")),
                name="check_active_to_greater_than_active_from",
            ),
        ]
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["rule", "code"], name="idx_dc_rule_code"),
            models.Index(fields=["rule", "active_from", "active_to"], name="idx_dc_dates"),
            models.Index(fields=["rule", "max_products_qty"], name="idx_dc_max_qty"),
            models.Index(fields=["rule", "current_used", "max_used"], name="idx_dc_usage"),
            models.Index(fields=["code"], name="idx_dc_code"),
        ]
