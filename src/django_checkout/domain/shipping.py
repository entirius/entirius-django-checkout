# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.cache import cache
from process_logger import ProcessLogger

from django_checkout.domain.cart_limiters import check_items_weight
from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.product import products_have_attribute
from django_checkout.enums import PriceType
from django_checkout.models.shipping_price_by_weight import ShippingPriceMatrix
from django_checkout.settings import CACHE_TTL, USE_CACHED_VIEWS

if TYPE_CHECKING:
    from django_checkout.models import Channel, ShippingOption

import hashlib
from typing import Optional

logger_process = ProcessLogger("CHECKOUT_CART")


def generate_cache_key(shipping_option_pk, sku_qty_list, channel_pk):
    concatenated_string = f"{','.join([str({x.sku}) + str({x.quantity}) for x in sku_qty_list])}|{channel_pk}"
    md5_hash = hashlib.md5(concatenated_string.encode()).hexdigest()
    key_data = f"shipping_option:{shipping_option_pk}|{md5_hash}"
    return key_data


def get_delivery_matrix_prices(
    shipping_option: Optional["ShippingOption"], sku_qty_list: list[SkuQuantityData], channel: "Channel"
):
    """
    This method handles poorly more than one modifier. It should be refactored.
    sku_qty_list: list of tuples (sku, qty)
    return  free_delivery_items,
            free_delivery_items_modifier,
            free_delivery_above_price,
            free_delivery_above_price_modifier,
            new_shipping_price,
            new_shipping_price_modifier
    """
    if not shipping_option:
        return [], [], None, None, None, None

    cache_key = generate_cache_key(shipping_option.pk, sku_qty_list, channel.pk)
    if USE_CACHED_VIEWS:
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

    match shipping_option.method.price_type:
        case PriceType.FIXED:
            free_delivery_items = [item.sku for item in sku_qty_list]
            free_delivery_items_modifier = []
            free_delivery_above_price = shipping_option.free_delivery_above_brutto
            free_delivery_above_price_modifier = None
            new_shipping_price = shipping_option.price_brutto
            new_shipping_price_modifier = Decimal(0.00)

            return_data = (
                free_delivery_items,
                free_delivery_items_modifier,
                free_delivery_above_price,
                free_delivery_above_price_modifier,
                new_shipping_price,
                new_shipping_price_modifier,
            )
        case PriceType.MATRIX:
            new_shipping_prices_modifier = []
            free_delivery_items_modifier = []

            # pobierz wszystkie shipping option matrix
            spm = shipping_option.shipping_price_matrixes.all()

            # sprawdź, czy i po jakich atrybutach cena matrix jest zdefiniowana
            spm_feature_attr_idx = spm.filter(filter_type=ShippingPriceMatrix.FilterType.ATTR)
            # items_weight, *_ = check_items_weight(sku_qty_list, channel)

            # iteruj po wszystkich featureach, które występują i sprawdzaj, czy jakikolwiek produkt go posiada
            attr_sku_list = None
            for spm_attr in spm_feature_attr_idx:
                attr_sku_list = products_have_attribute(channel.idx, sku_qty_list, spm_attr.idx, spm_attr.value)
                if attr_sku_list:
                    # przelicz wagę tylko dla produktów z atrybutem, klepnięte by Kloczi
                    items_weight, *_ = check_items_weight(attr_sku_list, channel)
                    # weź cenę z odpowiedniego przedziału wagowego
                    new_shipping_prices_modifier.extend(
                        spm_attr.shipping_price_option_matrix.all().get_price_by_weight_from_matrix(items_weight)
                        for _ in attr_sku_list
                    )
                    # oznacz, że ten produkt jest objęty modyfikatorem
                    free_delivery_items_modifier.extend(
                        item.sku for item in attr_sku_list if item.sku not in free_delivery_items_modifier
                    )

            # jeśli są zdefiniowane ceny dla atrybutów to weź najwyższą
            new_shipping_price_modifier = (
                max(new_shipping_prices_modifier) if new_shipping_prices_modifier else Decimal(0.00)
            )

            # znajdz sku które nie zostały przydzielone do liczenia po atrybutach
            if attr_sku_list:
                attr_sku_set = {attr_sku_data.sku for attr_sku_data in attr_sku_list}
                rest_sku_list = [sku_data for sku_data in sku_qty_list if sku_data.sku not in attr_sku_set]
            else:
                rest_sku_list = sku_qty_list

            spm_all = spm.filter(filter_type=ShippingPriceMatrix.FilterType.ALL_REST)

            # dla pozostałch produktów pobierz ogólną cenę matrix oznaczoną jako ALL
            new_shipping_price = Decimal(0.00)
            if spm_all.exists():
                items_weight, *_ = check_items_weight(rest_sku_list, channel)
                new_shipping_price = (
                    spm_all.first().shipping_price_option_matrix.all().get_price_by_weight_from_matrix(items_weight)
                )

            if not new_shipping_price and not new_shipping_price_modifier:
                logger_process.error(
                    f"Shipping option {shipping_option.pk} has no price defined for the "
                    f"given attributes and no default price for matrix"
                )
                return [], [], None, None, None, None

            free_delivery_above_brutto_prices = spm_all.values_list("free_delivery_above_brutto", flat=True)
            free_delivery_above_brutto_prices_modifier = spm_feature_attr_idx.values_list(
                "free_delivery_above_brutto", flat=True
            )
            free_delivery_above_price = (
                min(free_delivery_above_brutto_prices) if free_delivery_above_brutto_prices else None
            )
            free_delivery_above_price_modifier = (
                min(free_delivery_above_brutto_prices_modifier) if free_delivery_above_brutto_prices_modifier else None
            )

            free_delivery_items = [item.sku for item in sku_qty_list if item.sku not in free_delivery_items_modifier]
            return_data = (
                free_delivery_items,
                free_delivery_items_modifier,
                free_delivery_above_price,
                free_delivery_above_price_modifier,
                new_shipping_price,
                new_shipping_price_modifier,
            )
        case _:
            raise NotImplementedError("Unknown shipping_option price type")

    if USE_CACHED_VIEWS:
        cache.set(cache_key, return_data, CACHE_TTL)

    return return_data
