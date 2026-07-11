# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ObjectDoesNotExist
from django_pricemanager.output import get_product_price_for_country_and_currency
from django_regional.models import Country, Currency

from django_checkout import settings
from django_checkout.domain.dto.item import GratisData, ItemData
from django_checkout.enums import FeeType
from django_checkout.settings import USE_PRICE_TUNER_IN_CHECKOUT

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel
import logging

logger = logging.getLogger("process")


def determine_country_of_pricelist(country, validated_addresses):
    shipping_address = None
    shipping_country = None
    if settings.IS_VAT_OSS_TURN_ON:
        shipping_address = getattr(validated_addresses, "shipping_address", None)
        if shipping_address:
            shipping_country = getattr(shipping_address, "country_code", None)

    if shipping_country:
        return shipping_country
    else:
        return country


def fetch_prices(
    sku_list: list[str] | list[ItemData | GratisData],
    channel: "Channel",
    country_code,
    currency_code,
    validated_addresses=None,
    uid: str = None,
    vat_0: bool = False,
) -> tuple[dict[Any, Any], Any | None]:
    price_list_grouped_by_sku = {}
    country_code = determine_country_of_pricelist(country_code, validated_addresses)
    for item in sku_list:
        if isinstance(item, ItemData) or isinstance(item, GratisData):
            sku = item.sku
            sku_identifier = item.sku_identifier
        else:
            sku = item
            sku_identifier = item
        try:
            price = get_product_price_for_country_and_currency(
                channel_idx=channel.idx,
                product_sku=sku,
                country_code=country_code,
                currency_code=currency_code,
                uid=uid,
                vat_0=vat_0,
            )
            # pricemanager returns flat dict with "product" key directly (no "price" wrapper)
            price_data = price.get("price", price) if isinstance(price, dict) else price
            if price_data and "product" in price_data:
                price_list_grouped_by_sku[sku_identifier] = price_data
        except ObjectDoesNotExist:
            pass

    return price_list_grouped_by_sku, country_code


def tune_prices(
    price_list_grouped_by_sku: dict,
    channel: "Channel",
    country_code,
    currency_code,
    customer=None,
    validated_addresses=None,
):
    if not USE_PRICE_TUNER_IN_CHECKOUT:
        return price_list_grouped_by_sku, [], True

    try:
        from django_pricetuner.output import PriceDTO, PriceTuner

        country_code = determine_country_of_pricelist(country_code, validated_addresses)
        country = Country.objects.get(iso2=country_code)
        currency = Currency.objects.get(iso3=currency_code)

        pt = PriceTuner(channel.idx)
        user_group = customer.group if customer and customer.group else None
        pt.set_condition(group=user_group, price_country=country, price_currency=currency)

        prices = {}
        for sku, price in price_list_grouped_by_sku.items():
            if "uid" in price and price["uid"] is not None:
                if not settings.CUSTOMER_PRICES_PRICE_TUNER_IN_CHECKOUT:
                    continue

            prices_dto = [
                PriceDTO(name="gross", value=price["gross"]),
                PriceDTO(name="net", value=price["net"]),
                PriceDTO(name="special_gross", value=price["special_gross"]),
                PriceDTO(name="special_net", value=price["special_net"]),
            ]
            prices_dto = pt.tune_prices(prices_dto, sku=sku)
            prices[sku] = prices_dto

    except ImportError:
        return price_list_grouped_by_sku, [], True
    else:
        allow_for_discount_codes = True
        for sku, prices_dto in prices.items():
            for price_dto in prices_dto:
                price_list_grouped_by_sku[sku][price_dto.name] = price_dto.value
                allow = price_dto.is_rules_allows_discount_codes()
                if not allow:
                    allow_for_discount_codes = False
        return price_list_grouped_by_sku, prices, allow_for_discount_codes


def calc_fee_of_price(payment_method, price):
    if payment_method.fee_type == FeeType.FLAT_FEE and payment_method.fee_value != 0:
        fee_price = round(Decimal(payment_method.fee_value), 2)
    elif payment_method.fee_type == FeeType.PERCENTAGE_FEE:
        fee_price = round(Decimal(price * Decimal(payment_method.fee_value / 100)), 2)
    else:
        fee_price = Decimal(0.0)
    return fee_price


def add_fee_to_price(payment_method, price):
    fee_price = calc_fee_of_price(payment_method, price)
    return round(price + fee_price, 2)
