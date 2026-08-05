# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING

from django_pricemanager.output import validate_access_to_price_list
from django_utils.api.responses import ErrorInfo

from django_checkout.domain.dto.cart import CartRequest, CheckoutData
from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.prices import calc_fee_of_price
from django_checkout.domain.validators.address import validate_addresses
from django_checkout.domain.validators.cart import check_min_order_price, validate_cart_data
from django_checkout.domain.validators.delivery_date import validator_delivery_date
from django_checkout.domain.validators.item import get_item_list_status
from django_checkout.domain.validators.payment import validate_payment_method
from django_checkout.domain.validators.shipping import get_shipping_method_option, validate_shipping_method
from django_checkout.enums import ValidationStatus
from django_checkout.settings import CUSTOMS_THRESHOLD_ENABLED
from django_checkout.utils.api.utils import resolve_country, resolve_currency, resolve_language

if TYPE_CHECKING:
    from django_accounts.models.customer import Customer

    from django_checkout.models.channel import Channel


def clear_gratis_discounts(data: CartRequest, checkout_data: CheckoutData):
    if getattr(checkout_data.cart, "items", None):
        checkout_data.cart.items = [item for item in checkout_data.cart.items if not getattr(item, "is_gratis", False)]
    if getattr(data.cart, "items", None):
        data.cart.items = [item for item in data.cart.items if not getattr(item, "is_gratis", False)]
    return data, checkout_data


def process_cart(
    channel: "Channel",
    data: CartRequest,
    checkout_data: CheckoutData = None,
    customer: "Customer" = None,
    geo_country: str = None,
    split_order_data=None,
    cart=None,
    is_order_creation: bool = False,
    declarative_payment_method: bool = False,
) -> (CheckoutData, str):
    if checkout_data is None:
        checkout_data = CheckoutData.factory()

    messages = []
    language = resolve_language(channel, {"lang": data.language_code, "checkout_lang": checkout_data.language_code})

    country, currency = resolve_country(
        channel,
        {"country": data.country_code, "checkout_country": checkout_data.country_code, "geo_country": geo_country},
        customer,
    )
    if not currency:
        currency = resolve_currency(
            channel, {"currency": data.currency_code, "checkout_currency": checkout_data.currency_code}
        )

    validate_access_to_price_list(customer, channel.idx, country, currency, "Don't have access to create cart")
    validated_addresses, address_errors = validate_addresses(
        data.addresses if data.addresses is not None else None, checkout_data.addresses
    )
    if address_errors:
        messages.extend(address_errors)

    # Use raw `items` (not get_valid_items) because on a fresh POST /carts/ the
    # input is ItemData without a `status` attribute, so get_valid_items()
    # returns []. Validation happens later inside process_cart; here we just
    # need (sku, quantity) tuples for shipping/payment availability checks.
    item_data_list = (
        [SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in data.cart.items]
        if getattr(data.cart, "items", None)
        else (
            [SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in checkout_data.cart.items]
            if getattr(checkout_data.cart, "items", None)
            else []
        )
    )

    shipping_option, shipping_method, msg, need_full_address = get_shipping_method_option(
        data.shipping_method,
        validated_addresses.shipping_address if validated_addresses is not None else None,
        currency,
        channel,
        item_data_list,
        checkout_data.shipping_method,
        customer,
    )

    if isinstance(msg, list):
        messages.extend(msg)

    data, checkout_data = clear_gratis_discounts(data, checkout_data)

    if cart is not None and declarative_payment_method and data.payment_method is not None:
        from django_checkout.enums import PaymentProvider
        from django_checkout.models import PaymentMethod

        attaching_methods = PaymentMethod.objects.filter(
            channel=channel, provider__in=PaymentProvider.with_cart_attachments()
        )
        for method in attaching_methods:
            method.get_provider().clear_cart_attachments(cart)

    validated_payments, msgs, payment_methods = validate_payment_method(
        data.payment_method,
        validated_addresses.billing_address if validated_addresses is not None else None,
        channel,
        country,
        language,
        currency,
        shipping_option,
        item_data_list,
        checkout_data.payment_method,
        customer,
        cart_record=cart,
    )
    # validate_payment_method always returns lists. Downstream code in this
    # module currently operates on a single payment method (legacy). Unwrap
    # to first item for backward compat — extend here when multi-method
    # processing reaches discount/fee/order create paths below.
    validated_payment = validated_payments[0] if validated_payments else None
    payment_method = payment_methods[0] if payment_methods else None
    messages.extend(msgs)

    # Provider-specific cart attachment (e.g. voucher pay_code → CartVoucher rows).
    # Runs once per validate cycle, after payment_method validation succeeds.
    # Replaces previous Phase-1 side-effect in validate_vouchers_signal handler.
    if cart is not None and payment_methods:
        raw_payment_inputs = data.payment_method if data.payment_method else checkout_data.payment_method
        for pm in payment_methods:
            attach_errors = pm.get_provider().attach_to_cart(cart, payment_data=raw_payment_inputs)
            messages.extend(attach_errors)
    validated_cart_data, msg, cart_weight, validated_addresses = validate_cart_data(
        data,
        channel,
        language,
        currency,
        country,
        validated_addresses,
        shipping_method,
        shipping_option,
        payment_method,
        checkout_data,
        customer=customer,
        split_order_data=split_order_data,
        cart=cart,
        is_order_creation=is_order_creation,
    )
    if isinstance(msg, str):
        messages.append(msg)
    if isinstance(msg, list):
        messages.extend(msg)

    sku_qty_list = [
        SkuQuantityData(sku=item.sku, quantity=item.quantity) for item in validated_cart_data.get_valid_items()
    ]
    before_discounts = validated_cart_data.discounts
    validated_cart_data.discounts = [
        discount for discount in validated_cart_data.discounts if discount.status == ValidationStatus.VALID
    ]

    if invalid_discounts := [
        discount.code
        for discount in before_discounts
        if discount.status == ValidationStatus.INVALID and not discount.is_automatic
    ]:
        validated_cart_data.validation_status = get_item_list_status(
            [*validated_cart_data.items, *validated_cart_data.discounts]
        )
        messages.append(
            ErrorInfo(
                message="Some discounts are invalid",
                code="one_or_more_discount_invalid",
                affected_values=invalid_discounts,
                affected_field="cart.discounts.code",
            )
        )

    validated_shipping, msg = validate_shipping_method(
        channel,
        shipping_method,
        data.shipping_method if data.shipping_method is not None else checkout_data.shipping_method,
        shipping_option,
        validated_addresses.shipping_address if validated_addresses is not None else None,
        country,
        language,
        sku_qty_list,
        split_order_data=split_order_data,
    )
    if msg:
        messages.append(msg)

    order_total_too_low = False
    if validated_shipping is not None:
        if not split_order_data:
            # Check if any discount rule assigns free shipping
            free_shipping_in_discounts = any(
                True
                for elem in validated_cart_data.discounts
                if elem.free_shipping and hasattr(validated_cart_data, "discounts")
            )

            # Basic free shipping based on items
            if validated_shipping.free_delivery_above is not None:
                free_shipping_items_total_price = Decimal(0)
                for item in validated_cart_data.get_valid_items():
                    if item.sku in validated_shipping.free_delivery_items:
                        free_shipping_items_total_price += (
                            item.total_price - item.discount_amount
                            if channel.is_discount_after_tax()
                            else item.total_price_netto - item.discount_amount_netto
                        )

                if free_shipping_items_total_price >= validated_shipping.free_delivery_above:
                    validated_shipping.discount_amount = (
                        validated_shipping.discount_amount + validated_shipping.normal_price
                    )

                    validated_shipping.unit_price = validated_shipping.unit_price - validated_shipping.normal_price
                    if validated_shipping.unit_price < 0:
                        validated_shipping.unit_price = Decimal(0)

            # Free shipping based on modifiers
            if validated_shipping.free_delivery_above_modifier is not None:
                free_shipping_items_total_price = Decimal(0)
                for item in validated_cart_data.get_valid_items():
                    if item.sku in validated_shipping.free_delivery_modifier_items:
                        free_shipping_items_total_price += (
                            item.total_price - item.discount_amount
                            if channel.is_discount_after_tax()
                            else item.total_price_netto - item.discount_amount_netto
                        )
                if free_shipping_items_total_price >= validated_shipping.free_delivery_above_modifier:
                    validated_shipping.discount_amount = (
                        validated_shipping.discount_amount + validated_shipping.modifier_price
                    )

                    validated_shipping.unit_price = validated_shipping.unit_price - validated_shipping.modifier_price
                    if validated_shipping.unit_price < 0:
                        validated_shipping.unit_price = Decimal(0)

            # Add COD Free if not free delivery
            if free_shipping_in_discounts:
                validated_shipping.discount_amount = validated_shipping.unit_price
                validated_shipping.unit_price = Decimal(0)
            elif (
                validated_payment is not None
                and validated_payment.is_cash_on_delivery
                and validated_shipping.cash_on_delivery_fee is not None
                and validated_shipping.total_price > 0
            ):
                validated_shipping.unit_price = validated_shipping.unit_price + validated_shipping.cash_on_delivery_fee

        if shipping_option is not None:
            validated_shipping.total_price = validated_shipping.unit_price

            customs_scenario = getattr(validated_cart_data, "customs_threshold_scenario", None)
            if customs_scenario and CUSTOMS_THRESHOLD_ENABLED:
                from django_checkout.domain.customs_threshold import ABOVE_THRESHOLD
                from django_checkout.models.customs_threshold_config import CustomsThresholdConfig

                shipping_country = (
                    getattr(getattr(validated_addresses, "shipping_address", None), "country_code", None) or country
                )
                threshold_config = CustomsThresholdConfig.get_config(shipping_country)
                if (
                    threshold_config
                    and threshold_config.apply_threshold_to_shipping
                    and customs_scenario == ABOVE_THRESHOLD
                ):
                    original_rate = Decimal(validated_shipping.tax_rate or 0)
                    net_price = Decimal(validated_shipping.unit_price / (1 + original_rate)).quantize(Decimal("0.01"))
                    validated_shipping.unit_price = net_price
                    validated_shipping.total_price = net_price
                    validated_shipping.base_unit_price = net_price
                    validated_shipping.base_total_price = net_price
                    validated_shipping.tax_rate = Decimal(0)

            validated_shipping.unit_price_netto = Decimal(
                validated_shipping.unit_price / (1 + Decimal(validated_shipping.tax_rate or 0))
            ).quantize(Decimal("0.01"))
            validated_shipping.total_price_netto = validated_shipping.unit_price_netto
            validated_shipping.tax_amount = validated_shipping.unit_price - validated_shipping.unit_price_netto

        base_total = validated_cart_data.base_total_price + validated_shipping.base_total_price
        total = validated_cart_data.total_price + validated_shipping.total_price

        if not split_order_data:
            total_to_min_order_price, msg, order_total_too_low = check_min_order_price(channel, validated_cart_data)
        shipping_tax_rate = validated_shipping.tax_rate
        shipping_total_price = validated_shipping.total_price
    else:
        shipping_tax_rate = Decimal(0)
        shipping_total_price = Decimal(0)
        base_total = round(validated_cart_data.base_total_price, 2)
        total = round(validated_cart_data.total_price, 2)
        if not split_order_data:
            total_to_min_order_price, msg, order_total_too_low = check_min_order_price(channel, validated_cart_data)
    if msg:
        messages.append(msg)

    fee_price = Decimal(0)
    fee_tax_rate = Decimal(0)
    fee_tax_price = Decimal(0)

    if split_order_data:
        fee_price = split_order_data.fee_price
    elif payment_method:
        fee_price = calc_fee_of_price(payment_method, total)
        fee_tax_rate = validated_shipping.tax_rate if validated_shipping else Decimal(0)
        fee_tax_price = round(fee_price - (fee_price / (1 + fee_tax_rate)), 2) if fee_tax_rate else Decimal(0)

    total = round(total + fee_price, 2)
    base_total = round(base_total + fee_price, 2)
    total_to_min_order_price, msg, order_total_too_low = check_min_order_price(channel, validated_cart_data)
    if msg:
        messages.append(msg)

    # Voucher coverage check: if the only payment method is voucher AND voucher
    # balance did not cover the full cart total, the remaining amount is
    # unpaid. Block the cart until customer adds another payment method (e.g.
    # PayU) or applies an additional voucher. Frontend uses error.extra to
    # render UI prompt (e.g. "Brakuje X PLN — dodaj metodę płatności").
    #
    # Only runs when cart already exists in DB (`cart is not None`) — during the
    # first pass of a POST /carts/ flow the cart row doesn't exist yet, so
    # voucher cannot have been attached via VoucherPaymentProvider.attach_to_cart.
    # The view re-runs process_cart with cart=record after Cart.create — that
    # second pass produces the authoritative voucher_amount_applied for this check.
    voucher_coverage_insufficient = False
    if cart is not None and payment_methods:
        from django_checkout.enums import PaymentProvider

        non_voucher_pm = [
            pm
            for pm in payment_methods
            if not PaymentProvider.is_voucher(getattr(pm, "provider", None), getattr(pm, "code", None))
        ]
        remaining_to_pay = Decimal(str(total or 0))
        voucher_paid = Decimal(str(validated_cart_data.voucher_amount_applied or 0))
        if not non_voucher_pm and remaining_to_pay > Decimal("0"):
            voucher_coverage_insufficient = True
            cart_total_before_voucher = voucher_paid + remaining_to_pay
            messages.append(
                ErrorInfo(
                    code="voucher_insufficient_balance",
                    message=(
                        f"Voucher covers {voucher_paid} {currency} of {cart_total_before_voucher} {currency}. "
                        f"Add another payment method or another voucher to cover the remaining {remaining_to_pay} {currency}."
                    ),
                    affected_field="cart.payment_method",
                    extra={
                        "voucher_amount_applied": str(voucher_paid),
                        "cart_total": str(cart_total_before_voucher),
                        "remaining_to_pay": str(remaining_to_pay),
                        "currency_code": currency,
                    },
                )
            )
        elif non_voucher_pm and voucher_paid > Decimal("0") and remaining_to_pay <= Decimal("0"):
            # Voucher covers 100% — drop the now-redundant gateway method(s).
            drop_codes = {getattr(pm, "code", None) for pm in non_voucher_pm}
            payment_methods = [
                pm
                for pm in payment_methods
                if PaymentProvider.is_voucher(getattr(pm, "provider", None), getattr(pm, "code", None))
            ]
            validated_payments = [vp for vp in (validated_payments or []) if vp.code not in drop_codes]

    if validated_cart_data.tax_amount is not None:
        total_tax = (
            (fee_price + shipping_total_price)
            - ((fee_price + shipping_total_price) / (1 + (shipping_tax_rate or 0)))
            + validated_cart_data.tax_amount
        )
    else:
        total_tax = Decimal(0)

    if total_tax:
        total_tax = round(total_tax, 2)
    is_delivery_date_valid, msg, requested_delivery_date = validator_delivery_date(data, checkout_data)
    if msg:
        messages.append(msg)

    # Don't validate if order_total is too low for checkout if order is splited
    order_total_too_low = False if split_order_data else order_total_too_low
    validation_status = (
        ValidationStatus.VALID
        if (
            validated_cart_data.validation_status == ValidationStatus.VALID
            and validated_addresses is not None
            and validated_shipping is not None
            and validated_payment is not None
            and is_delivery_date_valid
            and not order_total_too_low
            and not voucher_coverage_insufficient
            and get_item_list_status([validated_shipping, validated_payment]) == ValidationStatus.VALID
        )
        else ValidationStatus.INVALID
    )

    if data.split_order is not None and isinstance(data.split_order, bool):
        split_order = data.split_order
    elif checkout_data.split_order is not None and isinstance(checkout_data.split_order, bool):
        split_order = checkout_data.split_order
    else:
        split_order = False
    messages = [msg for msg in messages if msg]
    return (
        CheckoutData(
            cart=validated_cart_data,
            addresses=validated_addresses,
            requested_delivery_date=requested_delivery_date,
            # Persist the FULL list of validated payment methods (multi-method
            # support: voucher + payu, etc.). Downstream code that needs only
            # the primary method reads CheckoutData.payment_method[0] or uses
            # the `selected_payment_method` property on Cart.
            payment_method=validated_payments,
            shipping_method=validated_shipping,
            total_to_min_order_price=total_to_min_order_price,
            base_total=base_total,
            fee_price=fee_price,
            fee_tax_price=fee_tax_price,
            fee_tax_rate=fee_tax_rate,
            total=total,
            need_full_address=need_full_address,
            total_tax=total_tax,
            validation_status=validation_status,
            language_code=language,
            currency_code=currency,
            country_code=country,
            split_order=split_order,
            split_by_feature_idx=data.split_by_feature_idx if data.split_by_feature_idx else None,
            split_by_attr_idx=data.split_by_attr_idx if data.split_by_attr_idx else None,
            original_order_id=data.original_order_id,
            comment=data.comment or checkout_data.comment,
            affiliate_code=data.affiliate_code or checkout_data.affiliate_code,
            customer_ip=data.customer_ip or checkout_data.customer_ip,
            custom_order_id=data.custom_order_id if data.custom_order_id else checkout_data.custom_order_id,
        ),
        messages,
    )
