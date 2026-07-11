# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django_checkout.domain.dto.item import ItemData
from django_checkout.models import Order, StockReservation


def release_stock_reservation(order: Order, item_list: list[ItemData] = None) -> None:
    if item_list:
        for item in item_list:
            StockReservation.objects.filter(
                stock__product__sku=item.sku, stock__reserved_quantity=item.quantity, order=order
            ).first().delete()
    else:
        StockReservation.objects.filter(order=order).delete()
