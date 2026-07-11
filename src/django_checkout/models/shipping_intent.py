# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_checkout.models import Order, ShippingMethod


class ShippingIntent(models.Model):
    order: "Order" = models.ForeignKey("Order", related_name="shipping_items", on_delete=models.CASCADE)
    method: "ShippingMethod" = models.ForeignKey("ShippingMethod", null=True, blank=True, on_delete=models.SET_NULL)
    # code in case method is removed from db
    code = models.CharField(max_length=20)
    tracking_number = models.CharField(max_length=255, null=True, blank=True)
    tracking_link = models.TextField(null=True, blank=True)

    objects = models.Manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order"], name="one_shipping_intent_per_order")]
