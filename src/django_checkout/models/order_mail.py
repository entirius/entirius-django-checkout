# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Log of the e-mails an order was sent: one row per order and kind, reused by a resend."""

from django.conf import settings
from django.db import models

from django_checkout.enums import OrderMailKind, OrderMailStatus


class OrderMail(models.Model):
    order = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="mails")
    kind = models.CharField(max_length=20, choices=OrderMailKind.choices)
    status = models.CharField(max_length=10, choices=OrderMailStatus.choices, default=OrderMailStatus.PENDING)
    recipient = models.TextField(blank=True)
    language = models.CharField(max_length=2, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    # Exception class and a trimmed message: never credentials or the message body.
    last_error = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "kind"], name="unique_order_mail_kind")]

    def __str__(self) -> str:
        return f"{self.order_id} | {self.kind} | {self.status}"
