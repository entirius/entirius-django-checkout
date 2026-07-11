# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import models

if TYPE_CHECKING:
    from django_regional.models import Country, Currency


class CustomsThresholdConfig(models.Model):
    """
    Per-country customs threshold configuration for B2C e-commerce.

    When a shipment goes to a configured country, per-item prices are compared
    against the threshold. If any item meets or exceeds the threshold, 0% VAT
    applies (border taxation). Otherwise the standard scheme VAT applies.

    Admin updates channel_to_threshold_rate monthly when exchange rate changes.
    Example (Norway VOEC): country=NO, threshold_value=3000,
    threshold_currency=NOK, channel_to_threshold_rate=11.24 (1 EUR = 11.24 NOK).

    Note: threshold_currency (e.g. NOK) does not need to be a checkout currency —
    it must exist in django_regional.Currency only as a reference entry.
    """

    country: Country = models.ForeignKey(
        "django_regional.Country",
        on_delete=models.CASCADE,
        help_text="Delivery country, e.g. Norway.",
    )
    threshold_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Per-item threshold in threshold_currency, e.g. 3000 (NOK).",
    )
    threshold_currency: Currency = models.ForeignKey(
        "django_regional.Currency",
        on_delete=models.CASCADE,
        help_text="Currency of the threshold value, e.g. NOK.",
    )
    channel_to_threshold_rate = models.DecimalField(
        max_digits=14,
        decimal_places=6,
        default=Decimal("1.000000"),
        help_text=(
            "How many threshold currency units equal 1 channel currency unit. "
            "E.g. EUR channel with NOK threshold: 11.24 (1 EUR = 11.24 NOK). "
            "Update monthly when exchange rate changes."
        ),
    )
    scheme_code = models.CharField(
        max_length=20,
        help_text="Scheme name shown to frontend, e.g. 'VOEC', 'GST', 'IOSS'.",
    )
    scheme_number = models.CharField(
        max_length=64,
        blank=True,
        help_text="Scheme registration number, e.g. 'NO12345VOEC'.",
    )
    apply_threshold_to_shipping = models.BooleanField(
        default=False,
        help_text=(
            "When enabled, shipping and payment fee VAT follows the threshold scenario: "
            "BELOW_THRESHOLD → standard tax rate; ABOVE_THRESHOLD → 0% VAT."
        ),
    )
    is_active = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Customs Threshold Config"
        verbose_name_plural = "Customs Threshold Configs"
        unique_together = ("country",)

    def __str__(self) -> str:
        return f"{self.country.iso2} | {self.scheme_code} | {self.threshold_value} {self.threshold_currency.iso3}"

    @classmethod
    def get_config(cls, country_code: str) -> CustomsThresholdConfig | None:
        return cls.objects.filter(country__iso2=country_code.upper(), is_active=True).first()
