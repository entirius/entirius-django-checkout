# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import logging
from typing import TYPE_CHECKING

from django_pim.models import Product

from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.models.limited_products import LimitedProducts

logger = logging.getLogger(__name__)

try:
    from django_pim.managers.product import get_volumes_by_sku
except ImportError:
    get_volumes_by_sku = None

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel
    from django_checkout.models.shipping_method import ShippingMethod


def get_product_available(
    sku_list: list[str], shipping_method: "ShippingMethod", payment_methods: list, channel: "Channel", customer
) -> tuple[set[str], bool]:
    """The SKUs no limit takes out for the shipping method and each of the cart's payment methods, and whether a guest
    may buy them. A voucher pays next to another method, so the limits of every method apply, not only entry [0]'s.
    """
    products_available = None
    allowed_guest_checkout = True
    for payment_method in {method.pk: method for method in payment_methods or []}.values() or [None]:
        products, allowed = LimitedProducts.objects.filter_products_by_limitation(
            sku_list, shipping_method, payment_method, channel, True if customer else False
        )
        products_available = set(products) if products_available is None else products_available & set(products)
        allowed_guest_checkout = allowed_guest_checkout and allowed
    return products_available, allowed_guest_checkout


def _quantities_by_sku(items: list[SkuQuantityData]) -> dict:
    """Pieces per SKU over every line: a gift is a line of its own next to a bought line of the same SKU."""
    quantities = {}
    for item in items:
        quantities[item.sku] = quantities.get(item.sku, 0) + item.quantity
    return quantities


def check_items_weight(items: list[SkuQuantityData], channel: "Channel"):
    """Weight of every piece together, whether a product lacks a weight, and the weight of one piece per SKU.

    A SKU can sit on two lines (bought and gift), so a line weighs its own quantity times the unit weight.
    """
    sku_list = [elem.sku for elem in items]
    data = Product.objects.filter(shop__idx=channel.idx, real_product__sku__in=sku_list).values(
        "real_product__sku", "real_product__weight"
    )
    quantities = _quantities_by_sku(items)
    result = [
        {
            "real_product__sku": item["real_product__sku"],
            "real_product__weight": item["real_product__weight"],
            "quantity": quantities.get(item["real_product__sku"], 0),
        }
        for item in data
    ]

    item_sum_weight = 0
    unit_weights = {}
    items_dont_have_weight = False
    for item in result:
        if not item["real_product__weight"]:
            items_dont_have_weight = True
            unit_weights[item["real_product__sku"]] = None
        else:
            item_sum_weight += item["quantity"] * item["real_product__weight"]
            unit_weights[item["real_product__sku"]] = item["real_product__weight"]

    return item_sum_weight, items_dont_have_weight, unit_weights


def check_cart_is_too_heavy(item_sum_weight: list[SkuQuantityData], shipping_method: "ShippingMethod"):
    if shipping_method is None:
        return False

    if shipping_method.max_weight:
        max_weight = float(shipping_method.max_weight)
    else:
        max_weight = 99999999999999
    items_are_too_heavy = max_weight < item_sum_weight

    return items_are_too_heavy


def check_items_volume(items: list[SkuQuantityData], channel: "Channel"):
    if get_volumes_by_sku is None:
        return 0, False, {}

    sku_list = list(dict.fromkeys(elem.sku for elem in items))
    try:
        volumes_by_sku = get_volumes_by_sku(channel.idx, sku_list)
    except Exception:
        logger.warning("Failed to fetch product volumes from PIM, skipping volume check")
        return 0, False, {}

    quantities = _quantities_by_sku(items)

    item_sum_volume = 0
    items_volume = {}
    items_dont_have_volume = False
    for sku in sku_list:
        volume = volumes_by_sku.get(sku)
        if not volume:
            items_dont_have_volume = True
            items_volume[sku] = None
        else:
            line_volume = quantities.get(sku, 0) * volume
            item_sum_volume += line_volume
            items_volume[sku] = line_volume

    return item_sum_volume, items_dont_have_volume, items_volume


def check_cart_volume_exceeded(total_volume, shipping_method: "ShippingMethod") -> bool:
    if shipping_method is None:
        return False

    if not shipping_method.max_volume:
        return False

    return float(shipping_method.max_volume) < total_volume
