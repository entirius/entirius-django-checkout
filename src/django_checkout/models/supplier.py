# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_accounts.models.customer import Customer

    from django_checkout.models.channel import Channel


class Supplier(models.Model):
    code = models.CharField(max_length=254)
    name = models.CharField(max_length=254)
    is_global = models.BooleanField(default=False)
    channel: "Channel" = models.ForeignKey(
        "Channel",
        related_name="channel_suppliers",
        verbose_name="channel",
        null=False,
        blank=False,
        on_delete=models.CASCADE,
    )
    objects = models.Manager()

    def __str__(self):
        return f"{self.name} [{self.code}]"

    class Meta:
        constraints = [
            # channel moze posiadac tylko jeden supplier globalny
            models.UniqueConstraint(
                fields=["channel"], condition=models.Q(is_global=True), name="unique_supplier_global_per_channel"
            )
        ]


class SupplierCustomer(models.Model):
    supplier: "Supplier" = models.ForeignKey(
        Supplier,
        related_name="supplier_customers",
        verbose_name="supplier",
        null=False,
        blank=False,
        on_delete=models.CASCADE,
    )
    customer: "Customer" = models.ForeignKey(
        "django_accounts.Customer",
        related_name="customer_suppliers",
        verbose_name="customer",
        null=False,
        blank=False,
        on_delete=models.CASCADE,
    )
    objects = models.Manager()
