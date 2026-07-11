# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django.core.exceptions import ObjectDoesNotExist
from django_pricemanager.output import get_product_price_for_country_and_currency

from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.dto.discount_items import DiscountItems, ExtendedItemData

# from django_checkout.domain.prices import fetch_prices
from django_checkout.models import Channel, DiscountRuleCode
from django_checkout.worker.discount_worker import apply_discount_rule


# Used by:
# django-omnibus
def calc_discount(
    rule: DiscountRuleCode, discounted_items: DiscountItems, currency_code, country_code=None, channel_idx=None
) -> CartData | DiscountItems:
    """This function can calculate discounts for product/products regardless of cart"""
    # discounted_items = DiscountItems(items=[DiscountItemData(sku="unit-sku-1", quantity=2)])
    try:
        channel = Channel.objects.get(idx=channel_idx)
    except ObjectDoesNotExist:
        raise ObjectDoesNotExist(f"Channel with idx {channel_idx} does not exist")
    prices_discounted_items = fetch_prices(
        items=discounted_items, channel=channel, currency_code=currency_code, country_code=country_code
    )
    result = apply_discount_rule(rule, prices_discounted_items, channel=channel, currency_code=currency_code)
    return result


def fetch_prices(items: DiscountItems, channel: "Channel", currency_code, country_code=None) -> DiscountItems:
    if not country_code:
        country_code = channel.default_country.iso2
    for idx, item in enumerate(items.items):
        try:
            product_price = get_product_price_for_country_and_currency(
                channel.idx, item.sku, country_code, currency_code
            )
            price_dict = product_price["price"]
            if price_dict is None:
                continue

            price = (
                price_dict.get("special_gross")
                if price_dict.get("special_gross") is not None
                else price_dict.get("gross")
            )

            if price is None:
                continue

            discount_item = ExtendedItemData(
                sku=item.sku,
                quantity=item.quantity,
                base_unit_price=price_dict.get("gross"),
                base_total_price=price_dict.get("gross") * item.quantity,
                special_total_price=(
                    price_dict.get("special_gross") * item.quantity
                    if price_dict.get("special_gross") is not None
                    else None
                ),
                unit_price=price,
                total_price=price * item.quantity,
                tax_rate=price_dict.get("tax_rate"),
                unit_tax_amount=round(price_dict.get("gross") * price_dict.get("tax_rate"), 2),
                total_tax_amount=round(price_dict.get("gross") * price_dict.get("tax_rate") * item.quantity, 2),
            )

            items.items[idx] = discount_item
        except ObjectDoesNotExist:
            pass
    return items
