# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_checkout.models.order import Order
    from django_checkout.models.stock import Stock


class StockReservation(models.Model):
    reserved_quantity = models.IntegerField(default=0)
    stock: "Stock" = models.ForeignKey("Stock", on_delete=models.CASCADE)
    order: "Order" = models.ForeignKey("Order", on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = models.Manager()
