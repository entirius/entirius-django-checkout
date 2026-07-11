# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.models import Stock


def get_quantities(channel_idx: str) -> dict[str, int]:
    query = (
        Stock.annotated.get_queryset(saleable_quantity_limit_per_order=False)
        .filter(product__channel__idx=channel_idx, supplier__channel__idx=channel_idx, supplier__is_global=True)
        .values("product__sku", "saleable_quantity")
    )

    quantity_grouped_by_sku = {elem["product__sku"]: int(elem["saleable_quantity"]) for elem in query}

    return quantity_grouped_by_sku
