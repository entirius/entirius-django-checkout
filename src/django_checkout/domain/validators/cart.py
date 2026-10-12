# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import replace
from decimal import Decimal
from itertools import zip_longest
from typing import TYPE_CHECKING, Any

from django_utils.api.errors import ErrorInfo
from django_utils.api.exceptions import BadRequest

from django_checkout import settings
from django_checkout.domain.bundle import resolve_bundle_prices
from django_checkout.domain.cart_limiters import (
    check_cart_is_too_heavy,
    check_cart_volume_exceeded,
    check_items_volume,
    check_items_weight,
    get_product_available,
)
from django_checkout.domain.custom import fetch_custom_details, fetch_custom_prices
from django_checkout.domain.dto.cart import CartData, CheckoutData
from django_checkout.domain.dto.item import GratisData, ItemData, ValidatedItemData
from django_checkout.domain.prices import fetch_prices, tune_prices
from django_checkout.domain.product import fetch_products_name
from django_checkout.domain.quantities import check_which_bundle_is_not_saleable, fetch_quantities
from django_checkout.domain.sale_offers import manage_offer_prices
from django_checkout.domain.validators.discounts import validate_discounts
from django_checkout.domain.validators.item import get_item_list_status
from django_checkout.enums import ItemStatus, ValidationStatus
from django_checkout.models import ModifiersForDiscountRule
from django_checkout.models.abstract_product_filter import PriceRestrictionType
from django_checkout.models.channel import DiscountApplyType
from django_checkout.signals import (
    compute_cart_voucher_total_signal,
    validate_items_signal,
    validate_vouchers_signal,
)
from django_checkout.utils.api.utils import merge_prices_dictionaries
from django_checkout.worker.discount_worker import (
    apply_discount_rule,
    calculate_and_valid_gratis,
    format_available_gratis,
    get_all_available_gratis_rules,
)
from django_checkout.worker.split_order.utils import generate_split_order_cart

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


def validate_cart_data(
    full_data: CheckoutData,
    channel: "Channel",
    language,
    currency,
    country,
    validated_addresses,
    shipping_method,
    shipping_option,
    payment_method,
    full_checkout_data: CheckoutData = None,
    customer=None,
    split_order_data=None,
    cart=None,
    is_order_creation=False,
) -> tuple[Any, Any] | tuple[CartData, list[Any], Any | None, Any]:
    email = None
    cart_checkout_data = full_checkout_data.cart
    data = full_data.cart
    cart_record = cart
    cart_record_id = getattr(cart_record, "cart_id", None)

    def validate_items(
        items: list[ItemData | GratisData],
        force_vat_0: bool | None = None,
    ) -> tuple[list[ValidatedItemData], list[Any], Any, bool, bool, Any, Any]:
        msg_item = []
        item_sum_weight = 0
        items_weight = {}
        limited_sku_list = []
        items_are_too_heavy = False
        items_dont_have_weight = True

        unique_keys = set()
        for item in reversed(items):
            extra_key = None
            if hasattr(item, "extra") and item.extra is not None:
                try:
                    import json

                    extra_key = json.dumps(item.extra, sort_keys=True)
                except Exception:
                    extra_key = str(item.extra)
            key = (item.sku, extra_key)
            if key not in unique_keys:
                unique_keys.add(key)
            else:
                items.remove(item)
        items_not_allowed_from_signal = []
        if settings.USE_VALIDATE_ITEMS_SIGNAL:
            responses = validate_items_signal.send(
                sender=None,
                items=[item.sku for item in items],
                cart=cart_record,
                cart_id=cart_record_id,
                is_order_creation=is_order_creation,
            )
            for _, response in responses:
                if response:
                    not_allowed, error_msg = response

                    if not_allowed:
                        for item in not_allowed:
                            items_not_allowed_from_signal.append(item)
                    msg_item.extend(error_msg)

        if settings.USE_VALIDATE_VOUCHERS_SIGNAL:
            voucher_responses = validate_vouchers_signal.send(
                sender=None,
                cart=cart_record,
                cart_id=cart_record_id,
                is_order_creation=is_order_creation,
            )
            for _, response in voucher_responses:
                if response:
                    invalid_vouchers, voucher_error_msg = response
                    msg_item.extend(voucher_error_msg)

        for item in items:
            custom_details = fetch_custom_details(item, channel, language)
            if custom_details:
                if item.extra is None:
                    item.extra = {}
                item.extra.update(custom_details)
                if source_sku := custom_details.get("source_product_sku"):
                    item.sku = source_sku

        sku_list = [item.sku for item in items]

        # bundle
        saleable_bundles, bundle_items, bundle_errors = check_which_bundle_is_not_saleable(
            sku_list, channel, items, customer, language=language
        )
        msg_item.extend(bundle_errors)
        saleable_quantity_by_sku = fetch_quantities(sku_list, channel, customer)
        saleable_quantity_by_sku.update(saleable_bundles)

        vat_0 = False
        if settings.TURN_ON_VIES_VALIDATION_AND_0_VAT_WHEN_VALID_AND_OUTSIDE_PL:
            if validated_addresses:
                if validated_addresses.billing_address:
                    if validated_addresses.billing_address.tax_id:
                        nip = validated_addresses.billing_address.tax_id
                        if nip:
                            try:
                                from django_vat_validator.output import get_nip_is_valid

                                try:
                                    is_valid, nip_data, vat_0 = get_nip_is_valid(nip, country, channel.idx)
                                except Exception:
                                    is_valid = False

                                if is_valid:
                                    validated_addresses.billing_address.vat_validation_status = True

                                if not is_valid:
                                    msg_item.append(
                                        ErrorInfo(
                                            code="invalid_vat_number",
                                            message="Invalid VAT number",
                                            affected_values=[nip],
                                            affected_field="addresses.billing_address.tax_id",
                                        )
                                    )
                                if nip[0:2].upper() != country.upper() and nip[:2].isalpha():
                                    msg_item.append(
                                        ErrorInfo(
                                            code="vat_number_and_country_mismatch",
                                            message="VAT number and country mismatch",
                                            affected_values=[nip],
                                            affected_field="addresses.billing_address.tax_id",
                                        )
                                    )
                                else:
                                    if is_valid:
                                        validated_addresses.billing_address.invoice_requested_status = True

                            except ImportError:
                                pass

        customs_threshold_scenario = None
        if force_vat_0 is not None:
            vat_0 = force_vat_0
        elif settings.CUSTOMS_THRESHOLD_ENABLED and not vat_0:
            shipping_country_code = (
                getattr(getattr(validated_addresses, "shipping_address", None), "country_code", None) or country
            )
            if shipping_country_code:
                from django_checkout.domain.customs_threshold import (
                    ABOVE_THRESHOLD,
                    determine_threshold_scenario,
                    get_threshold_info_message,
                )
                from django_checkout.models.customs_threshold_config import CustomsThresholdConfig

                config = CustomsThresholdConfig.get_config(shipping_country_code)
                if config:
                    prices_first_pass, _ = fetch_prices(
                        sku_list=items,
                        channel=channel,
                        country_code=country,
                        currency_code=currency,
                        validated_addresses=validated_addresses,
                        uid=customer.uid if customer else None,
                        vat_0=False,
                    )
                    prices_first_pass_custom = fetch_custom_prices(
                        items,
                        channel,
                        country,
                        currency,
                        validated_addresses,
                        vat_0=False,
                        cart_id=cart_record_id,
                    )
                    prices_first_pass = merge_prices_dictionaries(prices_first_pass, prices_first_pass_custom)
                    customs_threshold_scenario = determine_threshold_scenario(
                        prices_first_pass,
                        config.threshold_value,
                        config.channel_to_threshold_rate,
                    )
                    if customs_threshold_scenario == ABOVE_THRESHOLD:
                        vat_0 = True
                    msg_item.append(get_threshold_info_message(customs_threshold_scenario, config))

        prices_by_sku_custom = fetch_custom_prices(
            items,
            channel,
            country,
            currency,
            validated_addresses,
            vat_0=vat_0,
            cart_id=cart_record_id,
        )
        # get prices, quantity, name
        prices_by_sku, country_code = fetch_prices(
            sku_list=items,
            channel=channel,
            country_code=country,
            currency_code=currency,
            validated_addresses=validated_addresses,
            uid=customer.uid if customer else None,
            vat_0=vat_0,
        )
        prices_by_sku = resolve_bundle_prices(
            items=items,
            channel=channel,
            country=country_code,
            currency=currency,
            prices_by_sku=prices_by_sku,
            uid=customer.uid if customer else None,
        )
        prices_by_sku, msg_item_offer, error_items = manage_offer_prices(
            items, channel.idx, prices_by_sku, country_code, currency
        )
        msg_item = msg_item + msg_item_offer

        for item in items[:]:
            if item.sku in error_items:
                items.remove(item)
        prices_by_sku = merge_prices_dictionaries(prices_by_sku, prices_by_sku_custom)
        # price tuner
        prices_by_sku, tuned_prices, price_tuner_allow_for_discount_codes = tune_prices(
            prices_by_sku, channel, country, currency, customer, validated_addresses
        )

        names_by_sku = fetch_products_name(sku_list, language, channel)
        shipping_option_method = None
        if hasattr(shipping_option, "method"):
            shipping_option_method = shipping_option.method

        products_available, allowed_guest_checkout = get_product_available(
            sku_list, shipping_option_method, payment_method, channel, customer
        )
        item_sum_weight, items_dont_have_weight, items_weight = check_items_weight(items, channel)
        items_are_too_heavy = check_cart_is_too_heavy(item_sum_weight, shipping_option_method)
        item_sum_volume, items_without_volume, items_volume = check_items_volume(items, channel)
        items_volume_exceeded = check_cart_volume_exceeded(item_sum_volume, shipping_option_method)
        result = []
        for item in items:
            # wykluczone produkty za pomocą wagi

            if items_are_too_heavy:
                item_status = ItemStatus.INVALID
                msg_item.append(
                    ErrorInfo(
                        code="items_are_too_heavy",
                        message="Items are too heavy",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                    )
                )

            elif items_volume_exceeded:
                item_status = ItemStatus.INVALID
                msg_item.append(
                    ErrorInfo(
                        code="items_volume_exceeded",
                        message="Items volume exceeds shipping method limit",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                    )
                )

            elif item.sku in items_not_allowed_from_signal:
                item_status = ItemStatus.INVALID

            # wykluczone produkty za pomocą tabeli LimitedProducts per shipping_method
            elif item.sku not in products_available:
                item_status = ItemStatus.INVALID
                msg_item.append(
                    ErrorInfo(
                        code="items_are_limited",
                        message="Items are limited",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                    )
                )

            # Wykluczone produkty, gdy nie ma danych nt. stocków/cen
            elif item.sku_identifier not in prices_by_sku or item.sku not in saleable_quantity_by_sku:
                item_status = ItemStatus.INVALID
                msg_item.append(
                    ErrorInfo(
                        code="items_dont_have_stock_or_price",
                        message="Items dont have stock or price",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                    )
                )

            # wykluczone produkty, gdy nie ma dostępności produktów
            elif not saleable_quantity_by_sku[item.sku]["is_saleable"]:
                item_status = ItemStatus.OUT_OF_STOCK
                msg_item.append(
                    ErrorInfo(
                        code="items_are_not_saleable",
                        message="Items are not saleable",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                    )
                )
            elif item.quantity > saleable_quantity_by_sku[item.sku]["saleable_quantity"]:
                if not settings.ADD_MAX_AVAILABLE_QUANTITY:
                    item_status = ItemStatus.OUT_OF_STOCK
                else:
                    item_status = ItemStatus.VALID

                msg_item.append(
                    ErrorInfo(
                        code="items_quantity_limited_by_stock",
                        message="Items quantity limited by stock",
                        affected_values=[item.sku],
                        affected_field="cart.items.sku",
                        extra={"max_quantity": saleable_quantity_by_sku[item.sku]["saleable_quantity"]},
                    )
                )

            else:
                if shipping_option:
                    # wykluczone produkty, gdy nie mają informacji o wadzę
                    if items_dont_have_weight and shipping_option.method.max_weight is not None:
                        item_status = ItemStatus.INVALID
                        msg_item.append(
                            ErrorInfo(
                                code="items_dont_have_weight",
                                message="Items dont have weight",
                                affected_values=[item.sku],
                                affected_field="cart.items.sku",
                            )
                        )
                        message_added_dont_have_weight = True
                    else:
                        item_status = ItemStatus.VALID
                else:
                    item_status = ItemStatus.VALID

            if all(
                [item_status != ItemStatus.VALID, not channel.show_product_prices_when_invalid]
            ) or not prices_by_sku.get(item.sku_identifier, None):
                validated_item = ValidatedItemData.invalid_factory(
                    sku=item.sku,
                    quantity=item.quantity,
                    status=item_status,
                    is_gratis=False,
                    name=names_by_sku.get(item.sku, item.sku),
                    voucher_gift=getattr(item, "voucher_gift", None),
                )
                result.append(validated_item)
            else:
                name = names_by_sku.get(item.sku, item.sku)
                is_egible_for_special = prices_by_sku.get(item.sku_identifier, {}).get(
                    "is_egible_for_special_price", False
                )
                quantity = min(
                    item.quantity,
                    (
                        saleable_quantity_by_sku[item.sku]["saleable_quantity"]
                        if saleable_quantity_by_sku[item.sku]["saleable_quantity"] > 0
                        else 0
                    ),
                )

                if quantity != item.quantity:
                    msg_item.append(
                        ErrorInfo(
                            code="items_quantity_limited_by_stock",
                            message="Items quantity limited by stock",
                            affected_values=[item.sku],
                            affected_field="cart.items.sku",
                        )
                    )

                # special
                special_net = prices_by_sku[item.sku_identifier]["special_net"]
                special_unit_price = prices_by_sku[item.sku_identifier]["special_gross"]
                special_total_price = special_unit_price * quantity if special_unit_price is not None else None

                # netto
                unit_net = prices_by_sku[item.sku_identifier]["net"]
                unit_price_netto = (
                    unit_net if special_net is None or special_net == 0 or not is_egible_for_special else special_net
                )
                total_price_netto = unit_price_netto * quantity if unit_price_netto else None

                # special dates
                special_from_date = (
                    prices_by_sku[item.sku_identifier]["special_from_date"].strftime("%Y-%m-%d")
                    if item.sku_identifier in prices_by_sku
                    and "special_from_date" in prices_by_sku[item.sku_identifier]
                    and prices_by_sku[item.sku_identifier]["special_from_date"] is not None
                    else None
                )

                special_to_date = (
                    prices_by_sku[item.sku_identifier]["special_to_date"].strftime("%Y-%m-%d")
                    if item.sku_identifier in prices_by_sku
                    and "special_to_date" in prices_by_sku[item.sku_identifier]
                    and prices_by_sku[item.sku_identifier]["special_to_date"] is not None
                    else None
                )

                # base price
                base_unit_price = prices_by_sku[item.sku_identifier]["gross"]
                base_total_price = base_unit_price * quantity
                unit_price = (
                    base_unit_price
                    if special_unit_price is None or special_unit_price == 0 or not is_egible_for_special
                    else special_unit_price
                )
                total_price = unit_price * quantity

                # special percentage
                special_percent = (
                    (base_unit_price - special_unit_price) / base_unit_price * 100
                    if special_unit_price is not None and special_unit_price != 0
                    else None
                )

                # discount
                discount_amount = Decimal(0)
                discount_amount_netto = Decimal(0)
                discount_percent = None

                # tax_rate
                tax_rate = (
                    prices_by_sku[item.sku_identifier]["tax_rate"]
                    if "tax_rate" in prices_by_sku[item.sku_identifier]
                    else None
                )
                if tax_rate is not None:
                    unit_price_net = (
                        prices_by_sku[item.sku_identifier]["net"]
                        if any(
                            [
                                prices_by_sku[item.sku_identifier]["special_net"] is None,
                                not prices_by_sku[item.sku_identifier]["is_egible_for_special_price"],
                            ]
                        )
                        else prices_by_sku[item.sku_identifier]["special_net"]
                    )
                    tax_amount = round(abs(unit_price - unit_price_net), 2)
                else:
                    tax_amount = 0

                # TODO porzadek z tym ifem bo nie potrzebny jest cały, do tego ogarnac discount_amount i tax_amount
                is_gratis = False
                if isinstance(item, GratisData) and getattr(item, "is_gratis", False):
                    is_gratis = True
                    quantity = item.quantity
                    non_gratis_total_price = total_price
                    gratis_multiplier = Decimal(1 - (settings.GRATIS_PERCENT_DISCOUNT / 100))
                    is_minimal_price = unit_price * gratis_multiplier < settings.GRATIS_PRICE
                    gratis_price = Decimal(
                        settings.GRATIS_PRICE
                        if is_minimal_price or settings.GRATIS_MECHANISM == 2
                        else round(unit_price * gratis_multiplier, 2)
                    )
                    total_price = round(gratis_price * quantity, 2)
                    base_total_price = round(unit_price * quantity, 2)
                    special_unit_price = None
                    special_total_price = None
                    discount_percent = int(gratis_price / unit_price * 100)
                    special_percent = None
                    discount_amount = round(non_gratis_total_price - base_total_price, 2)
                    unit_price = round(gratis_price, 2)
                    special_from_date = None
                    special_to_date = None
                    if tax_rate:
                        unit_price_netto = round(unit_price / (1 + Decimal(tax_rate or 0)), 2)
                        total_price_netto = round(unit_price_netto * quantity, 2)
                        discount_amount_netto = round(discount_amount / (1 + Decimal(tax_rate or 0)), 2)
                    else:
                        unit_price_netto = None
                        total_price_netto = None
                        tax_amount = 0

                validated_item = ValidatedItemData(
                    sku=item.sku,
                    name=name,
                    extra=item.extra,
                    quantity=quantity,
                    status=item_status,
                    offer_price=item.offer_price if hasattr(item, "offer_price") else None,
                    voucher_gift=getattr(item, "voucher_gift", None),
                    base_unit_price=round(base_unit_price, 2),
                    base_total_price=round(base_total_price, 2),
                    special_unit_price=(
                        round(special_unit_price, 2)
                        if special_unit_price is not None and is_egible_for_special
                        else None
                    ),
                    special_total_price=(
                        round(special_total_price, 2)
                        if special_total_price is not None and is_egible_for_special
                        else None
                    ),
                    discount_percent=round(discount_percent) if discount_percent is not None else None,
                    special_percent=round(special_percent) if special_percent is not None else None,
                    discount_amount=round(discount_amount, 2),
                    discount_amount_netto=round(discount_amount_netto, 2),
                    unit_price=round(unit_price, 2),
                    total_price=round(total_price, 2),
                    tax_rate=tax_rate,
                    sub_items=bundle_items.get(item.sku, None),
                    total_weight=items_weight.get(item.sku, None),
                    unit_tax_amount=round(tax_amount, 2),
                    total_tax_amount=round(tax_amount * quantity, 2),
                    base_unit_tax_amount=round(tax_amount, 2),
                    base_total_tax_amount=round(tax_amount * quantity, 2),
                    special_from_date=special_from_date,
                    special_to_date=special_to_date,
                    unit_price_netto=round(unit_price_netto, 2),
                    total_price_netto=round(total_price_netto, 2),
                    is_gratis=is_gratis,
                )
                result.append(validated_item)

        return (
            result,
            msg_item,
            item_sum_weight,
            price_tuner_allow_for_discount_codes,
            allowed_guest_checkout,
            validated_addresses,
            customs_threshold_scenario,
        )

    def get_email_from_addresses(data):
        if (
            hasattr(data, "addresses")
            and hasattr(data.addresses, "shipping_address")
            and hasattr(data.addresses.shipping_address, "email")
        ):
            return data.addresses.shipping_address.email
        if (
            hasattr(data, "addresses")
            and hasattr(data.addresses, "billing_address")
            and hasattr(data.addresses.billing_address, "email")
        ):
            return data.addresses.billing_address.email
        return None

    if split_order_data:
        return *generate_split_order_cart(split_order_data, get_item_list_status), validated_addresses

    # Validate Items in Cart
    msg = []
    cart_weight = None
    validated_items = []
    validated_items_all = []
    available_data = None
    allow_for_discount_codes = True
    allowed_guest_checkout = True
    customs_threshold_scenario = None
    items_source = None
    if hasattr(data, "items") and data.items is not None:
        email = get_email_from_addresses(full_data)
        items_source = data.items
        (
            validated_items_all,
            msg,
            cart_weight,
            allow_for_discount_codes,
            allowed_guest_checkout,
            validated_addresses,
            customs_threshold_scenario,
        ) = validate_items(items_source)
        validated_items = [item for item in validated_items_all if item.status == ItemStatus.VALID]
        available_data = full_data
    elif hasattr(cart_checkout_data, "items") and cart_checkout_data.items is not None:
        email = get_email_from_addresses(full_checkout_data)
        items_source = cart_checkout_data.items

        (
            validated_items_all,
            msg,
            cart_weight,
            allow_for_discount_codes,
            allowed_guest_checkout,
            validated_addresses,
            customs_threshold_scenario,
        ) = validate_items(items_source)
        validated_items = [item for item in validated_items_all if item.status == ItemStatus.VALID]
        available_data = full_checkout_data

    total_price = sum(item.total_price for item in validated_items)
    total_price_validator_based_on = (
        total_price
        if channel.is_discount_after_tax()
        else sum(item.total_price - item.total_tax_amount for item in validated_items)
    )
    total_price_discount_from_netto_brutto = (
        total_price
        if channel.discount_mode_of_action_price_restriction_type == PriceRestrictionType.BRUTTO
        else sum(item.total_price - item.total_tax_amount for item in validated_items)
    )
    if not allow_for_discount_codes:
        msg.append(
            ErrorInfo(
                message="Price rules from PriceTuner dont allow discount codes", code="price_tuner_no_discount_codes"
            )
        )

    # Validate Discounts in Cart
    validated_discounts = []
    all_available_gratis_rules = []

    if data:
        if data.is_clear_discounts():
            data.discounts = []
            cart_checkout_data.discounts = []

    if cart_checkout_data:
        if cart_checkout_data.is_clear_discounts():
            cart_checkout_data.discounts = []

    if getattr(data, "discounts", False):
        validated_discounts = validate_discounts(
            data,
            total_price_validator_based_on,
            channel,
            customer=customer,
            email=email,
            allow_for_discount_codes=allow_for_discount_codes,
            currency=currency,
        )
    elif getattr(cart_checkout_data, "discounts", False):
        validated_discounts = validate_discounts(
            cart_checkout_data,
            total_price_validator_based_on,
            channel,
            customer=customer,
            email=email,
            allow_for_discount_codes=allow_for_discount_codes,
            currency=currency,
        )
    else:
        # jeżeli w koszyku nie ma discountów, sprawdź jeszcze czy można dodać te automatyczne "automatic applications" == True
        validated_discounts = validate_discounts(
            available_data.cart,
            total_price_validator_based_on,
            channel,
            customer=customer,
            email=email,
            currency=currency,
        )

    # Sort validated_discounts by priority to ensure correct application order
    # Lower priority value = higher priority (applied first)
    # None priority is treated as lowest priority (applied last)
    validated_discounts = sorted(
        validated_discounts,
        key=lambda x: (
            x[1].priority if x[1] and x[1].priority is not None else float("inf"),
            x[1].created_at if x[1] else None,
        ),
    )

    all_validated_discount_data = [vd[0] for vd in validated_discounts]
    # Validate Is cart egible to free shipment
    if allow_for_discount_codes and validated_discounts and shipping_method:
        for vd in validated_discounts:
            if hasattr(vd[1], "free_shipping"):
                vd[0].free_shipping = (
                    True
                    if all(
                        [
                            vd[1].free_shipping,
                            shipping_method.code in vd[1].free_shipping_methods.values_list("code", flat=True),
                        ]
                    )
                    else False
                )

    if allow_for_discount_codes and validated_discounts:
        for vd in validated_discounts:
            if hasattr(vd[1], "free_shipping_methods"):
                vd[0].free_shipping_methods = list(vd[1].free_shipping_methods.values_list("code", flat=True))

    # Validation status for Cart Data

    validation_status = (
        ValidationStatus.INVALID
        if len(validated_items_all) == 0
        else get_item_list_status([*validated_items_all, *all_validated_discount_data])
    )

    # Build Cart Data with sorted discounts
    cart = CartData(
        items=validated_items_all,
        discounts=all_validated_discount_data,  # Sorted by priority
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
        customs_threshold_scenario=customs_threshold_scenario,
    )
    # Apply discount rules in priority order (already sorted by priority)
    # This ensures that if gratis has higher priority (lower priority value),
    # it will be checked BEFORE price discounts are applied
    all_gratis = []
    previously_discounted_skus: set = set()
    prior_combine_rule_applied = False
    if settings.RESTRICT_COMBINE_BY_PRIORITY:
        # Sort by ORM priority ascending (lower number = higher priority = applied first).
        # validate_discounts returns user-codes before auto-codes regardless of priority;
        # priority-based exclusion requires a global priority order.
        validated_discounts.sort(
            key=lambda vd: (
                vd[1].priority if vd[1] is not None and getattr(vd[1], "priority", None) is not None else float("inf")
            )
        )
        cart.discounts = [vd[0] for vd in validated_discounts]
    for idx, (c_discount_rule, validated_discount) in enumerate(validated_discounts):
        if validated_discount:
            if (
                allow_for_discount_codes
                and validated_discount
                and c_discount_rule.modifier
                in [ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART, ModifiersForDiscountRule.GRATIS_STEPPED]
            ):
                # Check gratis eligibility based on CURRENT cart total_price
                # (after any higher-priority discounts have been applied)
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

                right = cart
            elif allow_for_discount_codes and validated_discount:
                is_combine = bool(getattr(validated_discount, "combine_with_other_rules", False))
                exclude_skus = None
                if (
                    settings.RESTRICT_COMBINE_BY_PRIORITY
                    and is_combine
                    and prior_combine_rule_applied
                    and previously_discounted_skus
                ):
                    exclude_skus = previously_discounted_skus

                pre_amounts = {item.sku: item.discount_amount or Decimal(0) for item in cart.items}

                right = apply_discount_rule(
                    rule=validated_discount,
                    cart_data=cart,
                    discount_idx=idx,
                    channel=channel,
                    exclude_skus=exclude_skus,
                    currency_code=currency,
                )

                if settings.RESTRICT_COMBINE_BY_PRIORITY and is_combine:
                    for item in cart.items:
                        if getattr(item, "is_gratis", False):
                            continue
                        after = item.discount_amount or Decimal(0)
                        if after > pre_amounts.get(item.sku, Decimal(0)):
                            previously_discounted_skus.add(item.sku)
                    prior_combine_rule_applied = True
            else:
                right = cart

    # Two-pass customs threshold re-evaluation: if discount brought per-item
    # price below threshold, re-run pricing with VAT and re-apply discounts.
    if items_source is not None:
        from django_checkout.domain.validators.customs_threshold_reeval import reevaluate_customs_threshold

        cart, customs_threshold_scenario, msg, all_gratis, _reeval_items = reevaluate_customs_threshold(
            cart=cart,
            customs_threshold_scenario=customs_threshold_scenario,
            items_source=items_source,
            validate_items_fn=validate_items,
            validated_discounts=validated_discounts,
            all_validated_discount_data=all_validated_discount_data,
            all_gratis=all_gratis,
            allow_for_discount_codes=allow_for_discount_codes,
            validated_addresses=validated_addresses,
            country=country,
            msg=msg,
            channel=channel,
            currency=currency,
            customer=customer,
        )
        if _reeval_items:
            validated_items = _reeval_items

    cart_copy_for_gratis = replace(cart)
    available_data.cart = cart_copy_for_gratis

    all_available_gratis_rules, amount_required_for_nearest_gratis_rule, amount_missing_for_nearest_gratis_rule = (
        get_all_available_gratis_rules(
            available_data,
            cart.total_price,
            channel,
            customer,
            email,
            country,
            currency,
            validated_discounts=validated_discounts,
        )
    )
    cart.amount_required_for_nearest_gratis_rule = (
        round(Decimal(amount_required_for_nearest_gratis_rule), 2) if amount_required_for_nearest_gratis_rule else None
    )
    cart.amount_missing_for_nearest_gratis_rule = (
        round(Decimal(amount_missing_for_nearest_gratis_rule), 2) if amount_missing_for_nearest_gratis_rule else None
    )
    cart.is_gratis_available = bool(all_available_gratis_rules)
    cart.available_gratis_rules = format_available_gratis(available_gratis_rules=all_available_gratis_rules)
    # przelicz jeszcze raz, ale z dodanymi gratisami
    for allow_and_picked_for_gratis in all_gratis:
        if isinstance(allow_and_picked_for_gratis, dict):
            items = []
            gratis_product = GratisData(
                sku=allow_and_picked_for_gratis["sku"], quantity=allow_and_picked_for_gratis["quantity"], is_gratis=True
            )
            if hasattr(data, "items") and data.items is not None:
                items = [item for item in data.items]
            elif hasattr(cart_checkout_data, "items") and cart_checkout_data.items is not None:
                items = [item for item in cart_checkout_data.items]

            items.append(gratis_product)
            (
                validated_items,
                msg,
                cart_weight,
                allow_for_discount_codes,
                allowed_guest_checkout,
                validated_addresses,
                _,
            ) = validate_items(items)
            cart.items = validated_items
            new_validated_items = None
            for idx, (c_discount_rule, validated_discount) in enumerate(validated_discounts):
                if allow_for_discount_codes and validated_discount:
                    new_validated_items = apply_discount_rule(
                        rule=validated_discount,
                        cart_data=cart,
                        discount_idx=idx,
                        skip_gratis=True,
                        channel=channel,
                        currency_code=currency,
                    )
            # zupdatuj inforamcje o cart
            if new_validated_items:
                cart.items = new_validated_items.items
            cart.discounts = [vd[0] for vd in validated_discounts]
            cart.validation_status = validation_status

    left = None
    if hasattr(data, "items") and data.items is not None:
        left = data
    elif hasattr(cart_checkout_data, "items") and cart_checkout_data is not None:
        left = cart_checkout_data

    if left is None:
        raise BadRequest(message="Cart doesn't have items in request or db. Items are needed in create request.")
    merged_cart = merge(left, cart)
    _apply_voucher_total(merged_cart, getattr(cart_record, "pk", None))
    return merged_cart, msg, cart_weight, validated_addresses


def merge(l_cart: "CartData", r_cart: "CartData") -> "CartData":
    def merge_items(l_item, r_item):
        """
        l_item is what comes in from the client or from database storage
        r_item is the result of l_item going through validation
        this function should return a validated item
        proper status should be assigned based on the differences between left and right
        """
        if l_item is None:
            return r_item
        elif r_item is None:
            return l_item
        else:
            ch_qty = False
            ch_price = False
            if r_item.status in [ItemStatus.INVALID, ItemStatus.OUT_OF_STOCK]:
                # item is invalid, there is no point in checking whether something changed
                pass
            else:
                # check if quantity changed for some reason
                if hasattr(l_item, "quantity") and l_item.quantity is not None and l_item.quantity != r_item.quantity:
                    ch_qty = True
                # check if price changed for some reason
                if (
                    hasattr(l_item, "base_unit_price")
                    and l_item.base_unit_price is not None
                    and l_item.base_unit_price != r_item.base_unit_price
                ):
                    ch_price = True
                if (
                    hasattr(l_item, "unit_price")
                    and l_item.unit_price is not None
                    and l_item.unit_price != r_item.unit_price
                ):
                    ch_price = True

            # Assign status based on difference between items
            if ch_qty and ch_price:
                status = ItemStatus.CHANGED_PRICE_AND_QTY
            elif ch_price:
                status = ItemStatus.CHANGED_PRICE
            elif ch_qty:
                status = ItemStatus.CHANGED_QTY
            else:
                status = r_item.status
            r_item.status = status

            return r_item

    merged = [merge_items(left, right) for left, right in zip_longest(l_cart.items, r_cart.items)]
    r_cart.items = merged
    return r_cart


def check_total_to_base_on(channel, checkout_data: CartData):
    match channel.discount_apply_type:
        case DiscountApplyType.AFTER:
            return checkout_data.total_price
        case DiscountApplyType.BEFORE:
            return checkout_data.total_price - checkout_data.tax_amount
    return checkout_data.total_price


def check_min_order_price(channel, validated_cart_data):
    order_total_too_low = False
    total_to_min_order_price = None
    msg = ""
    total_based_on = check_total_to_base_on(channel, validated_cart_data)
    # Voucher is a PAYMENT (like cash, card, payu) — not a discount. The customer
    # already paid for the voucher when it was issued, so its applied amount is
    # exempt from the min_order_price gate. Other reductions (discount_amount,
    # gratis) stay in total_based_on and still trigger the gate.
    voucher_paid = Decimal(str(validated_cart_data.voucher_amount_applied or 0))
    if channel.min_order_price:
        effective_total = Decimal(str(total_based_on)) + voucher_paid
        order_total_too_low = effective_total < Decimal(str(channel.min_order_price))
        if order_total_too_low:
            msg = ErrorInfo(code="total_not_min_order_price", message="Total is lower than min order price")
            total_to_min_order_price = str(
                round(Decimal(str(channel.min_order_price)), 2) - round(Decimal(str(total_based_on)) + voucher_paid, 2)
            )
    return total_to_min_order_price, msg, order_total_too_low


def _apply_voucher_total(cart_data, cart_record_id) -> None:
    """Reduce the cart TOTAL by the applied-voucher amount (tender semantics).

    A voucher is a payment, not a discount: line items keep their real sold
    price — they are persisted to order_body and exported (e.g. Magento), where
    a voucher-mutated price would under-report revenue and break amount
    reconciliation. Only cart-level total/netto/tax shrink; the tender itself is
    carried by ``voucher_amount_applied`` and its own PaymentIntent.

    Decoupled via ``compute_cart_voucher_total_signal``; no-op when module absent
    or USE_VALIDATE_VOUCHERS_SIGNAL is False.
    """
    if cart_record_id is None:
        return
    if not getattr(settings, "USE_VALIDATE_VOUCHERS_SIGNAL", False):
        return

    cart_items = [
        (it.sku, Decimal(str(it.total_price or 0))) for it in (cart_data.items or []) if getattr(it, "sku", None)
    ]
    line_totals: dict[str, Decimal] = {}
    for sku, line_total in cart_items:
        line_totals[sku] = line_totals.get(sku, Decimal("0")) + line_total

    responses = compute_cart_voucher_total_signal.send(sender=None, cart_id=cart_record_id, cart_items=cart_items)
    # Defense in depth: cap each amount to its own eligible line total. The voucher
    # module already caps, but checkout must not let a stale per_sku reduce more than
    # the eligible lines are worth — else voucher money discounts EXCLUDED products.
    total = Decimal("0")
    for _, response in responses:
        if response is None:
            continue
        if isinstance(response, dict):
            per_sku = response.get("per_sku")
            if isinstance(per_sku, dict) and per_sku:
                for sku, amount in per_sku.items():
                    capped = min(Decimal(str(amount or 0)), line_totals.get(sku, Decimal("0")))
                    if capped > 0:
                        total += capped
            else:
                total += Decimal(str(response.get("total") or 0))
        else:
            total += Decimal(str(response))

    if total <= 0:
        return
    cart_data.voucher_amount_applied = total

    # Items carry BASE price (the discount lives only in cart_data.discount_amount),
    # so never re-sum items here — reduce the already-discounted total directly.
    if cart_data.total_price is not None:
        old_total = Decimal(str(cart_data.total_price))
        reduction = min(total, old_total)
        new_total = old_total - reduction
        ratio = (new_total / old_total) if old_total > 0 else Decimal("0")
        cart_data.total_price = new_total
        if cart_data.total_netto_price is not None:
            cart_data.total_netto_price = (Decimal(str(cart_data.total_netto_price)) * ratio).quantize(Decimal("0.01"))
        if cart_data.tax_amount is not None:
            cart_data.tax_amount = (Decimal(str(cart_data.tax_amount)) * ratio).quantize(Decimal("0.01"))
