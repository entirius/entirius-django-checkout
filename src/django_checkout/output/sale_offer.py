# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.models.sale_offer import SaleOffer


# Used by matrix 2.7.0
def get_offer_price_range(product_sku, channel_idx, country_code, currency):
    min_price_offer, max_price_offer, *_ = SaleOffer.objects.get_offer_range_for_product_sku(
        product_sku, channel_idx, country_code, currency
    )
    return min_price_offer, max_price_offer
