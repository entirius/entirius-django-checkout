# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django_utils.api.errors import ErrorInfo

if TYPE_CHECKING:
    from django_checkout.models.customs_threshold_config import CustomsThresholdConfig

BELOW_THRESHOLD = "BELOW_THRESHOLD"
ABOVE_THRESHOLD = "ABOVE_THRESHOLD"


def determine_threshold_scenario(
    prices_by_sku: dict,
    threshold_value: Decimal,
    channel_to_threshold_rate: Decimal,
) -> str:
    """
    Return ABOVE_THRESHOLD if any item meets or exceeds the threshold,
    otherwise BELOW_THRESHOLD.

    Comparison uses NET price (pre-tax item value) because customs thresholds
    (e.g. VOEC 3000 NOK) are defined on the value of goods excluding local VAT.
    Using gross would incorrectly inflate the comparison when IS_VAT_OSS_TURN_ON
    applies local VAT (e.g. NO 25%) to the first-pass price fetch.

    Example: 267 EUR net * 11.24 = 3001 NOK >= 3000 NOK → ABOVE_THRESHOLD
    """
    for price_data in prices_by_sku.values():
        net = price_data.get("net") or Decimal(0)
        if net * channel_to_threshold_rate >= threshold_value:
            return ABOVE_THRESHOLD
    return BELOW_THRESHOLD


def build_post_discount_prices(cart_items: list) -> dict:
    """Build effective per-item NET prices after discount for threshold re-check."""
    prices = {}
    for item in cart_items:
        if not item.quantity:
            continue
        effective_net = (item.total_price - (item.discount_amount or Decimal(0))) / item.quantity
        prices[item.sku] = {"net": effective_net}
    return prices


def get_threshold_info_message(scenario: str, config: CustomsThresholdConfig) -> ErrorInfo:
    """
    Build an ErrorInfo customer message for the customs threshold scenario.
    Frontend reads extra.scheme_code and extra.message_key for display logic.
    """
    scheme_number = config.scheme_number if scenario == BELOW_THRESHOLD else None
    return ErrorInfo(
        code="customs_threshold_info",
        message=f"Customs threshold applies: {scenario}",
        extra={
            "scenario": scenario,
            "scheme_code": config.scheme_code,
            "scheme_number": scheme_number,
            "message_key": f"checkout.customs_threshold.{scenario.lower()}",
        },
    )
