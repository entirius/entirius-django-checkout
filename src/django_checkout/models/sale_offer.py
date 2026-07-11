# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import logging

from django.db import models
from django_regional.models import Country, Currency
from django_utils.models.base_model import BaseModel

from django_checkout.domain.prices import fetch_prices
from django_checkout.models.product_representation import ProductRepresentation

logger = logging.getLogger("process")


class SaleOfferQuerySet(models.QuerySet):
    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query, using, hints)


class SaleOfferManager(models.Manager):
    def get_queryset(self):
        return SaleOfferQuerySet(self.model, using=self._db)

    def _get_active_sale_offers(self):
        return self.get_queryset().filter(is_active=True).select_related("product")

    def get_offer_range_for_product_sku(
        self, product_sku: str, channel_idx, country_code_iso2, currency_iso3, prices_by_sku: dict = None
    ):
        sale_offer = (
            self._get_active_sale_offers().filter(product__sku=product_sku, product__channel__idx=channel_idx).first()
        )

        if not sale_offer:
            return None, None, False, True

        if not prices_by_sku:
            prices_by_sku, country_code = fetch_prices(
                [product_sku], sale_offer.product.channel, country_code_iso2, currency_iso3
            )

        if prices_by_sku.get(product_sku, None) is None:
            return None, None, True, False

        so_price = sale_offer.sale_offer_price.filter(
            country_code__iso2=country_code_iso2, currency__iso3=currency_iso3
        ).first()

        # Nie ma oferty dla tego produktu w tym kraju i walucie, skip
        if not so_price:
            return None, None, True, True

        max_price_offer = so_price.price_to
        min_price_offer = so_price.price_from

        # Jeśli nie ma ceny maksymalnej, to ustawiamy cenę maksymalną jako cena produktu
        if not max_price_offer:
            max_price_offer = prices_by_sku[product_sku]["gross"]

        if max_price_offer < min_price_offer:
            logger.warning(
                f"Max price {max_price_offer} is lower than min price {min_price_offer} for sku {product_sku}"
            )
            return None, None, True, True

        if max_price_offer is None or min_price_offer is None:
            logger.warning(f"Max price {max_price_offer} or min price {min_price_offer} is None for sku {product_sku}")
            return None, None, True, True

        gross = prices_by_sku[product_sku]["gross"]
        special_gross = prices_by_sku[product_sku]["special_gross"]
        if max_price_offer < gross or False if not special_gross else max_price_offer < special_gross:
            logger.warning(
                f"Price {max_price_offer} is less than gross price {prices_by_sku[product_sku]['gross']} or special gross price {prices_by_sku[product_sku]['special_gross']} for sku {product_sku}"
            )
            return None, None, True, True

        if min_price_offer > gross or False if not special_gross else min_price_offer > special_gross:
            logger.warning(
                f"Price {min_price_offer} is higher than gross price {prices_by_sku[product_sku]['gross']} or special gross price {prices_by_sku[product_sku]['special_gross']} for sku {product_sku}"
            )
            return None, None, True, True

        return min_price_offer, max_price_offer, True, True


class SaleOffer(BaseModel):
    product: ProductRepresentation = models.ForeignKey(ProductRepresentation, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    objects = SaleOfferManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["product"], name="unique_sale_offer_per_product")]


class SaleOfferPrice(BaseModel):
    sale_offer: SaleOffer = models.ForeignKey(SaleOffer, on_delete=models.CASCADE, related_name="sale_offer_price")
    currency: Currency = models.ForeignKey(Currency, on_delete=models.CASCADE)
    country_code: Country = models.ForeignKey(Country, on_delete=models.CASCADE)
    price_from = models.DecimalField(max_digits=12, decimal_places=2)
    price_to = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    objects = models.Manager()

    class Meta:
        unique_together = ("sale_offer", "currency", "country_code")

    @property
    def actual_price(self):
        try:
            from django_pricemanager.output import (
                get_product_price_for_country_and_currency,
            )

            price = get_product_price_for_country_and_currency(
                channel_idx=self.sale_offer.product.channel.idx,
                product_sku=self.sale_offer.product.sku,
                country_code=self.country_code.iso2,
                currency_code=self.currency.iso3,
            )
            price = price["price"] if "price" in price else {}
            price = price.get("special_gross", None) or price.get("gross", None) or None
            return price
        except ImportError:
            return None
