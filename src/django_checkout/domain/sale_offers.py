# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django_utils.api.responses import ErrorInfo

from django_checkout.models.sale_offer import SaleOffer


def manage_offer_prices(items, channel_idx, prices_by_sku, country_code, currency):
    msg_item = []
    offers_error_range = []
    item_dont_have_base_price = []
    dont_have_offer_price = []
    for item in reversed(items):
        min_price_offer, max_price_offer, is_sale_offer, have_base_price = (
            SaleOffer.objects.get_offer_range_for_product_sku(
                item.sku, channel_idx, country_code, currency, prices_by_sku
            )
        )
        if not have_base_price:
            item_dont_have_base_price.append(item.sku)
            continue

        if not is_sale_offer:
            continue

        if any([not min_price_offer, not max_price_offer]):
            offers_error_range.append(item.sku)
            continue

        if not item.offer_price:
            dont_have_offer_price.append(item.sku)
            continue

        if any([item.offer_price < min_price_offer, item.offer_price > max_price_offer]):
            offers_error_range.append(item.sku)
            continue

        tax_rate = prices_by_sku[item.sku]["tax_rate"]
        prices_by_sku[item.sku]["gross"] = item.offer_price
        if prices_by_sku[item.sku]["special_gross"]:
            prices_by_sku[item.sku]["special_gross"] = item.offer_price
        prices_by_sku[item.sku]["net"] = round(Decimal(item.offer_price / (1 + (tax_rate or 0))), 2)
        prices_by_sku[item.sku]["special_net"] = round(Decimal(item.offer_price / (1 + (tax_rate or 0))), 2)

    if offers_error_range:
        msg_item = [
            ErrorInfo(
                code="offer_price_out_of_range",
                message="Offer price is out of range",
                affected_values=[offers_error_range],
                affected_field="cart.items.offer_price",
            )
        ]

    if item_dont_have_base_price:
        msg_item = [
            ErrorInfo(
                code="product_dont_have_base_price",
                message="Product don't have base price",
                affected_values=[item_dont_have_base_price],
                affected_field="cart.items",
            )
        ]

    if dont_have_offer_price:
        msg_item = [
            ErrorInfo(
                code="offer_price_not_provided",
                message="Offer price not provided, product got standard price",
                affected_values=[dont_have_offer_price],
                affected_field="cart.items.offer_price",
            )
        ]
    return prices_by_sku, msg_item, offers_error_range + dont_have_offer_price
