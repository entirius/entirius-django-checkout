# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid
from typing import TYPE_CHECKING

from django.db import models
from django_utils.models.base_model import BaseModel

if TYPE_CHECKING:
    from django_checkout.models.order import Order


class PaymentRedirect(BaseModel):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order: "Order" = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="payment_redirects")
    target_url = models.TextField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()

    def __str__(self) -> str:
        return f"PaymentRedirect(order={self.order_id}, token={self.token})"
