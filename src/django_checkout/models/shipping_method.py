# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from django_checkout.enums import PriceType

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


class ShippingMethod(models.Model):
    class MethodType(models.TextChoices):
        DEFAULT = "default", _("Default")
        INPOST = "inpost", _("Inpost")
        PICKUP_POINT = "pickup_point", _("Pickup Point")
        DELIVERY = "delivery", _("Delivery")
        VIRTUAL = "virtual", _("Virtual")

    channel: "Channel" = models.ForeignKey("Channel", on_delete=models.CASCADE)
    method_type = models.CharField(max_length=24, choices=MethodType.choices, default=MethodType.DEFAULT)
    dpm_code = models.JSONField(null=True, blank=True)
    code = models.CharField(max_length=20)
    image = models.ImageField(upload_to="pic", null=True, blank=True)
    name_t9n = models.JSONField(blank=True, null=True)
    description_t9n = models.JSONField(blank=True, null=True)
    price_type = models.CharField(max_length=24, choices=PriceType.choices, default=PriceType.FIXED)
    max_weight = models.PositiveIntegerField(validators=[MinValueValidator(0)], blank=True, null=True)
    max_volume = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        blank=True,
        null=True,
        help_text="Maximum total volume for this shipping method.",
    )
    priority = models.PositiveIntegerField(default=0)
    position = models.PositiveIntegerField(default=0, blank=False, null=False)
    objects = models.Manager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "code"], name="unique_shipping_method_code_per_channel")
        ]

    def __str__(self):
        return f"[{self.channel.idx}] | {self.code}"
