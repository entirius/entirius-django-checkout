# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django.db import models

# F
from django.db.models import Q

from django_checkout import settings
from django_checkout.utils import payment_entries


def _set_localized_name(entry: dict, names_by_code: dict) -> None:
    name = names_by_code.get(entry.get("code"))
    if name is not None:
        entry["name"] = name


class OrderManager(models.Manager):
    @staticmethod
    def localized_method_names(channel, language) -> tuple[dict, dict]:
        """Localized shipping and payment method names, keyed by code, for one channel.

        Two queries for a whole page of orders. Resolving the shipping name per order used
        to go through ``Order.selected_shipping_method``, which runs the full ShippingOption
        availability chain (country, currency, stock, volume, matrix prices) only to read
        ``ShippingMethod.name_t9n`` off the end of it.
        """
        # Local: models/order.py imports this manager before it imports PaymentMethod, and
        # payment_method pulls in domain.payment_provider, which reaches back into models.
        from django_checkout.models.payment_method import PaymentMethod
        from django_checkout.models.shipping_method import ShippingMethod

        def names(model):
            return {m.code: (m.name_t9n or {}).get(language) for m in model.objects.filter(channel=channel)}

        return names(ShippingMethod), names(PaymentMethod)

    @staticmethod
    def update_shipping_and_payment_method_name(order_body, *, method_names):
        """Overwrite the method names stored in order_body with their localized versions.

        ``method_names`` is the ``localized_method_names`` pair, hoisted out of the caller's
        loop so the two lookups are not repeated for every order on a page. Required and
        keyword-only on purpose: the old signature was ``(request, order, order_body)``, and
        a stale caller must fail with a TypeError rather than silently bind ``order_body``
        to the wrong parameter.

        A code with no matching method row keeps the name persisted at order time.
        """
        shipping_names, payment_names = method_names

        shipping_method = order_body.get("shipping_method")
        if isinstance(shipping_method, dict):
            _set_localized_name(shipping_method, shipping_names)
        # payment_method became a list with the multi-method (voucher) refactor;
        # payment_entries tolerates both shapes and hands back live references.
        for entry in payment_entries(order_body):
            _set_localized_name(entry, payment_names)
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
