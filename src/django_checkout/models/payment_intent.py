# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import TYPE_CHECKING

from django.db import models

from django_checkout.enums import PaymentIntentStatus
from django_checkout.utils.gateway_payload import scrub_gateway_payload

if TYPE_CHECKING:
    from django_checkout.models.order import Order
    from django_checkout.models.payment_method import PaymentMethod


PAYLOAD_FIELDS = ("provider_request", "provider_notify")


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

    def save(self, *args, **kwargs) -> None:
        # Data minimisation: a payload is stored only through the allowlist (ids, amounts, currencies, statuses,
        # timestamps), whoever writes it. Providers verify signatures on the raw request, never on these copies.
        update_fields = kwargs.get("update_fields")
        for name in PAYLOAD_FIELDS:
            if update_fields is None or name in update_fields:
                setattr(self, name, scrub_gateway_payload(getattr(self, name)))
        super().save(*args, **kwargs)

    def anonymize(self) -> bool:
        """Reduce payloads stored before ``save()`` scrubbed them; True if one held something the allowlist drops.

        The columns (amount, currency, external_order_id, payment_status, code) stay: accounting needs them.
        """
        if all(scrub_gateway_payload(getattr(self, name)) == getattr(self, name) for name in PAYLOAD_FIELDS):
            return False
        self.save(update_fields=list(PAYLOAD_FIELDS))  # save() scrubs them
        return True


#
# @receiver(pre_save, sender=PaymentIntent)
# def change_order_status(sender, instance, **kwargs):
#     payment_intent_object = PaymentIntent.objects.filter(id=instance.id).first()
#     if payment_intent_object is not None:
#         if instance.payment_status == PaymentIntentStatus.COMPLETE:
#             payment_intent_object.order.modify_status(OrderStatus.CONFIRMED)
