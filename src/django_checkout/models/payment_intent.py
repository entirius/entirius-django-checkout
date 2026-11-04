# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

from django_checkout.enums import PaymentIntentStatus
from django_checkout.utils import REDACTED
from django_checkout.utils.gateway_payload import scrub_gateway_payload

if TYPE_CHECKING:
    from django_checkout.models.order import Order
    from django_checkout.models.payment_method import PaymentMethod


class PaymentIntent(models.Model):
    order: "Order" = models.ForeignKey("Order", related_name="payment_items", on_delete=models.CASCADE)
    method: "PaymentMethod" = models.ForeignKey("PaymentMethod", null=True, blank=True, on_delete=models.SET_NULL)
    payment_status = models.CharField(
        max_length=20, default=PaymentIntentStatus.NEW, choices=PaymentIntentStatus.choices
    )
    # code in case method is removed from db
    code = models.CharField(max_length=40)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)
    external_order_id = models.CharField(max_length=255, null=True, blank=True)
    redirect_url = models.TextField(null=True, blank=True)
    provider_notify = models.JSONField(null=True, blank=True)
    provider_request = models.JSONField(null=True, blank=True)

    objects = models.Manager()

    class Meta:
        # Allow multi-method payments (e.g. voucher + payu): unique per (order, code)
        # — one PaymentIntent per provider per order, but multiple providers OK.
        constraints = [models.UniqueConstraint(fields=["order", "code"], name="one_intent_per_order_code")]

    def anonymize(self) -> bool:
        """Keep only ids, amounts, statuses and timestamps of the gateway payloads, redact the payment link.

        True if there was anything to do. The link is the gateway's payment page or, for BLIK, a copy of the body's
        ``continue_url`` (redacted with the body); only the order's creation answer hands it out. The columns
        (amount, currency, external_order_id, payment_status, code) stay: accounting needs them.
        """
        holds_link = bool(self.redirect_url) and self.redirect_url != REDACTED
        if self.provider_request is None and self.provider_notify is None and not holds_link:
            return False
        self.provider_request = scrub_gateway_payload(self.provider_request)
        self.provider_notify = scrub_gateway_payload(self.provider_notify)
        if holds_link:
            self.redirect_url = REDACTED
        self.save(update_fields=["provider_request", "provider_notify", "redirect_url"])
        return True


#
# @receiver(pre_save, sender=PaymentIntent)
# def change_order_status(sender, instance, **kwargs):
#     payment_intent_object = PaymentIntent.objects.filter(id=instance.id).first()
#     if payment_intent_object is not None:
#         if instance.payment_status == PaymentIntentStatus.COMPLETE:
#             payment_intent_object.order.modify_status(OrderStatus.CONFIRMED)
