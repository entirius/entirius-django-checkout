# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

from django_checkout.models import SplitOrderMechanism

if TYPE_CHECKING:
    from django_checkout.models.cart import Cart
    from django_checkout.models.channel import Channel
    from django_checkout.models.order import Order


class SplitOrderLink(models.Model):
    channel: "Channel" = models.ForeignKey("Channel", null=True, on_delete=models.SET_NULL)

    original_cart: "Cart" = models.ForeignKey(
        "Cart", null=True, blank=True, on_delete=models.SET_NULL, related_name="original_cart_link"
    )
    original_order: "Order" = models.ForeignKey(
        "Order", null=True, blank=True, on_delete=models.PROTECT, related_name="original_order"
    )
    split_cart: "Cart" = models.ForeignKey(
        "Cart", null=True, blank=True, on_delete=models.PROTECT, related_name="split_cart"
    )
    split_order: "Order" = models.ForeignKey(
        "Order", null=True, blank=True, on_delete=models.PROTECT, related_name="split_order"
    )
    split_order_mechanism = models.CharField(
        max_length=45, default=SplitOrderMechanism.MODIFY_AND_CREATE, choices=SplitOrderMechanism.choices
    )
    split_by_feature_idx = models.CharField(max_length=100, null=False, blank=False)
    split_by_attribute_value = models.CharField(max_length=100, null=True, blank=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    objects = models.Manager()

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

    class Meta:
        db_table = "split_order_link"
        verbose_name = "Split Order Link"
        verbose_name_plural = "Split Order Links"
        ordering = ["-created"]
        unique_together = ("original_cart", "split_by_feature_idx", "split_by_attribute_value")
