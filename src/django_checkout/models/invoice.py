# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_checkout.models.order import Order


class Invoice(models.Model):
    """
    Invoice powinno być w OMS
    Kloczi mi kazał :(
    """

    order: "Order" = models.ForeignKey(
        "django_checkout.Order", related_name="invoice", null=True, on_delete=models.SET_NULL
    )
    invoice_id = models.UUIDField(default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=255, null=True, blank=True)
    invoice_base64 = models.TextField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    objects = models.Manager()

    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
