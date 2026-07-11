# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django_checkout import settings
from django_checkout.domain.prices import fetch_prices, tune_prices


def calc_base_unit_price_for_gratis(sku_list, channel, customer, country, currency, validated_addresses):
    prices_by_sku, country_code = fetch_prices(sku_list, channel, country, currency, validated_addresses)
    prices_by_sku, tuned_prices, price_tuner_allow_for_discount_codes = tune_prices(
        prices_by_sku, channel, country, currency, customer, validated_addresses
    )
    sku_price_data = {}
    for item in sku_list:
        if not prices_by_sku.get(item, None):
            continue

        is_egible_for_special = prices_by_sku[item]["is_egible_for_special_price"]
        special_unit_price = prices_by_sku[item]["special_gross"]
        base_unit_price = prices_by_sku[item]["gross"]
        unit_price = (
            base_unit_price
            if special_unit_price is None or special_unit_price == 0 or not is_egible_for_special
            else special_unit_price
        )
        gratis_multiplier = Decimal(1 - (settings.GRATIS_PERCENT_DISCOUNT / 100))
        is_minimal_price = unit_price * gratis_multiplier < settings.GRATIS_PRICE
        base_unit_price = (
            round(unit_price * gratis_multiplier, 2)
            if not is_minimal_price and settings.GRATIS_MECHANISM == 1
            else settings.GRATIS_PRICE
        )
        sku_price_data[item] = base_unit_price

    return sku_price_data
