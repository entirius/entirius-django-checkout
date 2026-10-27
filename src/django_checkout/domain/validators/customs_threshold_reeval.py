# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""
Two-pass customs threshold re-evaluation after discounts.

When first pass determined ABOVE_THRESHOLD (vat_0=True, prices are NET),
discounts may bring per-item effective price below the threshold.
If so, re-run the pricing pipeline with VAT and re-apply discounts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django_checkout import settings
from django_checkout.domain.customs_threshold import (
    BELOW_THRESHOLD,
    build_post_discount_prices,
    determine_threshold_scenario,
    get_threshold_info_message,
)
from django_checkout.domain.dto.cart import CartData
from django_checkout.domain.validators.item import get_item_list_status
from django_checkout.enums import ItemStatus, ValidationStatus
from django_checkout.models import ModifiersForDiscountRule
from django_checkout.models.customs_threshold_config import CustomsThresholdConfig
from django_checkout.worker.discount_worker import apply_discount_rule, calculate_and_valid_gratis

if TYPE_CHECKING:
    from collections.abc import Callable


def reevaluate_customs_threshold(
    cart: CartData,
    customs_threshold_scenario: str | None,
    items_source: list,
    validate_items_fn: Callable,
    validated_discounts: list,
    all_validated_discount_data: list,
    all_gratis: list,
    allow_for_discount_codes: bool,
    validated_addresses: Any,
    country: str,
    msg: list,
    channel: Any,
    currency: str,
    customer: Any = None,
) -> tuple[CartData, str | None, list, list, list]:
    """
    Check post-discount prices against customs threshold.
    If ABOVE flips to BELOW, re-run validate_items with force_vat_0=False,
    rebuild cart, and re-apply discounts.

    Returns (cart, customs_threshold_scenario, msg, all_gratis, validated_items).
    """
    if customs_threshold_scenario != "ABOVE_THRESHOLD" or not settings.CUSTOMS_THRESHOLD_ENABLED:
        return cart, customs_threshold_scenario, msg, all_gratis, []

    ship_country = getattr(getattr(validated_addresses, "shipping_address", None), "country_code", None) or country
    config = CustomsThresholdConfig.get_config(ship_country) if ship_country else None
    if not config:
        return cart, customs_threshold_scenario, msg, all_gratis, []

    post_prices = build_post_discount_prices(cart.get_valid_items())
    post_scenario = determine_threshold_scenario(post_prices, config.threshold_value, config.channel_to_threshold_rate)
    if post_scenario != BELOW_THRESHOLD:
        return cart, customs_threshold_scenario, msg, all_gratis, []

    # --- Second pass: re-price with VAT ---
    (
        validated_items_all,
        msg_pass2,
        cart_weight,
        allow_for_discount_codes,
        allowed_guest_checkout,
        validated_addresses,
        _,
    ) = validate_items_fn(items_source, force_vat_0=False)
    validated_items = [item for item in validated_items_all if item.status == ItemStatus.VALID]

    # Replace threshold info message
    msg = [m for m in msg if not (hasattr(m, "code") and m.code == "customs_threshold_info")]
    msg.append(get_threshold_info_message(BELOW_THRESHOLD, config))
    msg.extend(m for m in msg_pass2 if not (hasattr(m, "code") and m.code == "customs_threshold_info"))

    # Rebuild cart with VAT-inclusive prices
    validation_status = (
        ValidationStatus.INVALID
        if len(validated_items_all) == 0
        else get_item_list_status([*validated_items_all, *all_validated_discount_data])
    )
    cart = CartData(
        items=validated_items_all,
        discounts=all_validated_discount_data,
        total_weight=cart_weight,
        available_gratis_rules=None,
        is_gratis_available=None,
        amount_required_for_nearest_gratis_rule=None,
        amount_missing_for_nearest_gratis_rule=None,
        allowed_guest_checkout=allowed_guest_checkout,
        base_total_price=sum(item.base_total_price for item in validated_items),
        base_netto_price=sum(item.total_price_netto for item in validated_items),
        total_price=sum(item.total_price for item in validated_items),
        discount_amount=sum(item.discount_amount for item in validated_items),
        validation_status=validation_status,
        total_netto_price=sum(item.total_price_netto for item in validated_items if item.total_price_netto),
        tax_amount=sum(item.total_tax_amount for item in validated_items if item.total_tax_amount),
        customs_threshold_scenario=BELOW_THRESHOLD,
    )

    # Re-apply same discount rules with new prices
    all_gratis = []
    for idx, (c_discount_rule, validated_discount) in enumerate(validated_discounts):
        if not validated_discount:
            continue
        if allow_for_discount_codes and c_discount_rule.modifier in [
            ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
            ModifiersForDiscountRule.GRATIS_STEPPED,
        ]:
            gratis = calculate_and_valid_gratis(
                discount=validated_discount,
                cart_data=cart,
                channel=channel,
                discount_idx=idx,
                currency_code=currency,
                customer=customer,
            )
            if not gratis:
                cart.discounts[idx].status = ItemStatus.INVALID
            else:
                all_gratis.append(gratis)
        elif allow_for_discount_codes:
            apply_discount_rule(
                rule=validated_discount, cart_data=cart, discount_idx=idx, channel=channel, currency_code=currency
            )

    return cart, BELOW_THRESHOLD, msg, all_gratis, validated_items
