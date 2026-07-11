# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_pim.models import Product

    from django_checkout.models.order import Order


class Item(models.Model):
    order: "Order" = models.ForeignKey("Order", null=True, related_name="items", on_delete=models.CASCADE)
    product: "Product" = models.ForeignKey("django_pim.Product", null=True, blank=True, on_delete=models.SET_NULL)
    sku = models.CharField(max_length=100)
    name = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=6, decimal_places=2)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    additional_info = models.JSONField(null=True, blank=True)
    preorder = models.BooleanField(default=False)
    preorder_date = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()

    def __str__(self):
        return f"{self.sku}"

    @property
    def origin_sku(self):
        return self.additional_info.get("origin_sku", self.sku)
