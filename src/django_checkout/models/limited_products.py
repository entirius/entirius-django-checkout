# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

from django_checkout.enums import AssociationChoices, AuthenticationState
from django_checkout.models.managers.shipping_product_manager import ProductLimit

if TYPE_CHECKING:
    from django_checkout.models.payment_method import PaymentMethod
    from django_checkout.models.shipping_method import ShippingMethod


class LimitedProducts(models.Model):
    idx = models.CharField(db_index=True, max_length=40)
    value = models.CharField(max_length=255, null=True, blank=True)
    authentication_state = models.CharField(
        max_length=24, choices=AuthenticationState.choices, default=AuthenticationState.ALL
    )
    shipping_method: "ShippingMethod" = models.ForeignKey(
        "ShippingMethod", on_delete=models.CASCADE, null=True, blank=True
    )
    payment_method: "PaymentMethod" = models.ForeignKey(
        "PaymentMethod", on_delete=models.CASCADE, null=True, blank=True, default=None
    )
    association_type = models.CharField(
        max_length=24, choices=AssociationChoices.choices, default=AssociationChoices.SKU
    )
    is_active = models.BooleanField(default=True)
    objects = ProductLimit()

    def __str__(self):
        return f"{self.association_type} | {self.idx}"

    class Meta:
        verbose_name = "Limited product"
        verbose_name_plural = "Limited products"
        unique_together = [
            ("idx", "value", "shipping_method", "association_type"),
            ("idx", "value", "payment_method", "association_type"),
        ]
        indexes = [
            models.Index(
                fields=["is_active", "shipping_method", "payment_method", "authentication_state", "association_type"],
                name="idx_limitedproducts_combo",
            )
        ]
