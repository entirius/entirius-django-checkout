# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class UsedCoupon(BaseModel):
    """
    Model przechowujacy historie uzytych kuponow rabatowych.
    Sluzy do szybkiego sprawdzania limitow uzyc kuponu per uzytkownik.
    """

    channel = models.ForeignKey("Channel", on_delete=models.CASCADE, db_index=True)
    order = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="used_coupons")
    customer = models.ForeignKey(
        "django_accounts.Customer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        db_index=True,
    )
    shipping_email = models.EmailField(db_index=True, help_text="Email z shipping address")
    billing_email = models.EmailField(db_index=True, help_text="Email z billing address")
    discount_code = models.CharField(max_length=255, db_index=True)

    class Meta:
        db_table = "django_checkout_used_coupon"
        indexes = [
            models.Index(fields=["channel", "billing_email", "discount_code"], name="idx_used_coupon_billing"),
            models.Index(fields=["channel", "shipping_email", "discount_code"], name="idx_used_coupon_shipping"),
            models.Index(fields=["channel", "customer", "discount_code"], name="idx_used_coupon_customer"),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        email = self.billing_email or self.shipping_email or "no-email"
        return f"{email} - {self.discount_code} (Order: {self.order_id})"

    @classmethod
    def get_usage_count_by_email(cls, channel, email, discount_code, order_statuses):
        """
        Zlicza ile razy dany email uzyl konkretnego kuponu w okreslonych statusach zamowien.
        Sprawdza zarówno billing_email jak i shipping_email.
        """
        from django.db.models import Q

        return cls.objects.filter(
            Q(billing_email=email) | Q(shipping_email=email),
            channel=channel,
            discount_code=discount_code,
            order__order_status__in=order_statuses,
        ).count()

    @classmethod
    def get_all_usage_counts_by_email(cls, channel, email, order_statuses):
        """
        Zwraca slownik {kod_kuponu: liczba_uzyc} dla danego emaila.
        Sprawdza zarówno billing_email jak i shipping_email.
        """
        from django.db.models import Q

        results = (
            cls.objects.filter(
                Q(billing_email=email) | Q(shipping_email=email),
                channel=channel,
                order__order_status__in=order_statuses,
            )
            .values("discount_code")
            .annotate(count=models.Count("id"))
        )
        return {item["discount_code"]: item["count"] for item in results}
