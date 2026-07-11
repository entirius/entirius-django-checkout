# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_checkout.models.order import Order
    from django_checkout.models.shipping_option import ShippingOption


class Shipping(models.Model):
    order: "Order" = models.ForeignKey("Order", related_name="shipment_items", on_delete=models.CASCADE)
    method: "ShippingOption" = models.ForeignKey("ShippingOption", null=True, blank=True, on_delete=models.SET_NULL)
    # code in case method is removed from the database
    code = models.CharField(max_length=40)
    total_price = models.DecimalField(max_digits=8, decimal_places=2)

    objects = models.Manager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order"], name="one_shipping_per_order")]
