# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django_checkout.models import Channel, Stock


def get_quantities(channel_idx: str) -> dict[str, int]:
    query = (
        Stock.annotated.get_queryset(saleable_quantity_limit_per_order=False)
        .filter(product__channel__idx=channel_idx, supplier__channel__idx=channel_idx, supplier__is_global=True)
        .values("product__sku", "saleable_quantity")
    )

    quantity_grouped_by_sku = {elem["product__sku"]: int(elem["saleable_quantity"]) for elem in query}

    return quantity_grouped_by_sku


def get_bundle_quantities(skus: list[str], channel_idx: str) -> dict[str, int]:
    """How many of each bundle among `skus` its components allow, counted the way the cart counts a bundle line:
    the bundle's own Stock row does not decide. A bundle with limits counts its required and default components.
    SKUs that are not bundles are left out, so a caller can lay the result over its per-SKU quantities."""
    from django_checkout.domain.dto.item import ItemData
    from django_checkout.domain.quantities import check_which_bundle_is_not_saleable

    channel = Channel.objects.filter(idx=channel_idx).first()
    if not channel or not skus:
        return {}
    # One of each: the answer is how many bundles the stock covers, not whether a given quantity fits.
    items = [ItemData(sku=sku, quantity=Decimal(1), offer_price=None, extra=None, sub_items=None) for sku in skus]
    bundles, _, _ = check_which_bundle_is_not_saleable(list(skus), channel, items)
    return {sku: int(bundle["saleable_quantity"]) for sku, bundle in bundles.items()}
