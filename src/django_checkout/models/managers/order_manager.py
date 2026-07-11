# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django.db import models

# F
from django.db.models import Q

from django_checkout import settings
from django_checkout.utils.api.utils import resolve_language


class OrderManager(models.Manager):
    @staticmethod
    def update_shipping_and_payment_method_name(request, order, order_body):
        language = resolve_language(request.channel, request.GET)
        if order.selected_shipping_method is not None:
            name_shipping = order.selected_shipping_method.method.name_t9n.get(language, None)
            if name_shipping is not None:
                order_body["shipping_method"]["name"] = name_shipping
        # payment_method became a list with the multi-method (voucher) refactor; tolerate
        # the legacy single-dict shape and set each entry's localized name from its matching
        # PaymentMethod (by code), falling back to the order's first selected method.
        payment_method = order_body.get("payment_method")
        if isinstance(payment_method, dict):  # legacy single-object orders
            payment_method = [payment_method]
        if isinstance(payment_method, list):
            methods_by_code = {m.code: m for m in order.selected_payment_methods}
            for entry in payment_method:
                if not isinstance(entry, dict):
                    continue
                method = methods_by_code.get(entry.get("code")) or order.selected_payment_method
                if method is not None:
                    name_payment = method.name_t9n.get(language, None)
                    if name_payment is not None:
                        entry["name"] = name_payment
        return order_body

    def have_previous_order_by_email(self, channel, customer_email):
        """
        Sprawdza czy klient z danym emailem ma poprzednie zamówienia.
        Optymalizacja: najpierw sprawdza billing_email/shipping_email (bez JOIN),
        potem dopiero customer__user__email (z JOIN).
        """
        if customer_email is None:
            return False

        base_filter = {
            "channel": channel,
            "order_status__in": settings.ORDER_STATUSES_QUALIFIED_TO_FIRST_ORDER,
        }
        if self.filter(
            Q(billing_email=customer_email) | Q(shipping_email=customer_email),
            **base_filter,
        ).exists():
            return True

        return self.filter(
            customer__user__email=customer_email,
            **base_filter,
        ).exists()

    def have_previous_order_with_discount_code(self, channel, customer_email):
        """
        Zwraca słownik {kod_kuponu: liczba_użyć} dla danego emaila.
        Używa tabeli UsedCoupon dla szybszych zapytań.
        """
        if customer_email is None:
            return {}

        from django_checkout.models import UsedCoupon

        return UsedCoupon.get_all_usage_counts_by_email(
            channel=channel,
            email=customer_email,
            order_statuses=settings.ORDER_STATUSES_QUALIFIED_TO_DISCOUNT_MAX_PER_USER,
        )
