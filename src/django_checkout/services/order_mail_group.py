# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The orders of one purchase: a split purchase is every part created from one original cart."""

from django_checkout.models import Order, SplitOrderLink


def purchase_orders(order: Order) -> list[Order]:
    """The order alone, or every part of its split purchase in link creation order (two queries at most)."""
    link = SplitOrderLink.objects.filter(split_order=order).exclude(original_cart=None).first()
    if link is None:
        return [order]
    links = (
        SplitOrderLink.objects.filter(original_cart_id=link.original_cart_id, split_order__isnull=False)
        .select_related("split_order")
        .order_by("created", "pk")
    )
    return [item.split_order for item in links] or [order]


def lead_order(order: Order) -> Order:
    """The part that processed the payment: the first of the purchase."""
    return purchase_orders(order)[0]
