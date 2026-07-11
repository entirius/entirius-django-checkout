# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models
from django.db.models import UniqueConstraint
from django.db.models.functions import Lower
from idx_normalizator import validate_sku

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


class ProductRepresentation(models.Model):
    sku = models.CharField(db_index=True, max_length=128, null=False)
    channel: "Channel" = models.ForeignKey("Channel", on_delete=models.CASCADE, null=False)
    origin_sku = models.CharField(max_length=100, null=True, blank=True)

    objects = models.Manager()

    def save(self, *args, **kwargs):
        validate_sku(self.sku)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.sku} at {self.channel}"

    class Meta:
        ordering = ["sku"]
        verbose_name_plural = "products representations"
        constraints = [
            # pytanie: czy potrzebne jest by channel?
            UniqueConstraint(Lower("sku"), "channel", name="unique_checkout_product_sku_in_channel")
        ]
