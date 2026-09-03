# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import asdict
from decimal import Decimal

from django_utils.api.exceptions import BadRequest
from django_utils.settings import HEADER_COUNTRY
from process_logger import ProcessLogger

from django_checkout.domain.cart import process_cart
from django_checkout.domain.dto.cart import (
    AddressData,
    CartRequest,
    CheckoutData,
    PaymentData,
    ShippingData,
    ValidationStatus,
)
from django_checkout.domain.validators.order import OrderValidation
from django_checkout.enums import (
    CartStatus,
    PaymentProvider,
    SplitOrderPaymentFeeMechanism,
    SplitOrderShippingCostMechanism,
)
from django_checkout.models import Cart, Channel, Order, SplitOrderMechanism
from django_checkout.models.splited_order_link import SplitOrderLink

logger_process = ProcessLogger("SPLIT_ORDER")


def _settling_payment_method(payment_methods):
    """Return the payment method that settles a split part.

    ``payment_method`` is a list — a cart may carry a voucher next to a gateway method —
    while a split part is created against a single method, so the list has to be resolved
    to one entry before ``asdict()`` sees it (``asdict()`` on the list itself raises
    ``TypeError: asdict() should be called on dataclass instances``).

    """
    methods = [method for method in (payment_methods or []) if method is not None]
    if not methods:
        return None
    for method in methods:
        if not PaymentProvider.is_voucher(getattr(method, "provider", None), getattr(method, "code", None)):
            return method
    return methods[0]


def divide_number(number, parts):
    if number is None:
        return [None for part in range(parts)]
    base_value = ((number * 100) // parts) / 100
    rest = number - base_value * parts
    values = []
    for idx, part in enumerate(range(parts)):
        if idx == 0:
            values.append(round(base_value + rest, 2))
        else:
            values.append(round(base_value, 2))
    return values


class SplitOrderData:
    def __init__(self, validated_items, all_validated_discount_data, cart_weight, validated_shipping, fee_price):
        self.validated_items = validated_items
        self.all_validated_discount_data = all_validated_discount_data
        self.cart_weight = cart_weight
        self.validated_shipping = validated_shipping
        self.fee_price = fee_price


def prepare_shipping_method_for_split_order(order_as_data, parts, channel):
    match channel.split_order_shipping_cost_mechanism:
        case SplitOrderShippingCostMechanism.ATTRIBUTE_BASED_SHIPPING_COST_ASSIGNMENT:
            shipping_price = {
                "base_unit_price": [order_as_data.shipping_method.base_unit_price, 0],
                "base_total_price": [order_as_data.shipping_method.base_total_price, 0],
                "discount_amount": [order_as_data.shipping_method.discount_amount, 0],
                "unit_price": [order_as_data.shipping_method.unit_price, 0],
                "total_price": [order_as_data.shipping_method.total_price, 0],
                "tax_rate": [order_as_data.shipping_method.tax_rate, 0],
                "cash_on_delivery_fee": [order_as_data.shipping_method.cash_on_delivery_fee, 0],
            }

        case SplitOrderShippingCostMechanism.SHIPPING_COST_EQUALLY_DIVIDED:
            shipping_price = {
                "base_unit_price": divide_number(order_as_data.shipping_method.base_unit_price, parts),
                "base_total_price": divide_number(order_as_data.shipping_method.base_total_price, parts),
                "discount_amount": divide_number(order_as_data.shipping_method.discount_amount, parts),
                "unit_price": divide_number(order_as_data.shipping_method.unit_price, parts),
                "total_price": divide_number(order_as_data.shipping_method.total_price, parts),
                "tax_rate": divide_number(order_as_data.shipping_method.tax_rate, parts),
                "cash_on_delivery_fee": divide_number(order_as_data.shipping_method.cash_on_delivery_fee, parts),
            }
        case _:
            shipping_price = None
    return shipping_price


def prepare_payment_fee_for_split_order(cart_as_data, parts, channel):
    match channel.split_order_payment_fee_mechanism:
        case SplitOrderPaymentFeeMechanism.ATTRIBUTE_BASED_PAYMENT_FEE_ASSIGNMENT:
            payment_fees = {"fee_price": [cart_as_data.fee_price, 0]}

        case SplitOrderPaymentFeeMechanism.PAYMENT_FEE_EQUALLY_DIVIDED:
            payment_fees = {"fee_price": divide_number(cart_as_data.fee_price, parts)}
        case _:
            payment_fees = None
    return payment_fees


def change_shipping_method_for_split_order(cart_as_data, shipping_price, part, parts):
    cart_as_data.shipping_method.base_unit_price = shipping_price["base_unit_price"][part]
    cart_as_data.shipping_method.base_total_price = shipping_price["base_total_price"][part]
    cart_as_data.shipping_method.discount_amount = shipping_price["discount_amount"][part]
    cart_as_data.shipping_method.unit_price = shipping_price["unit_price"][part]
    cart_as_data.shipping_method.total_price = shipping_price["total_price"][part]
    cart_as_data.shipping_method.tax_rate = shipping_price["tax_rate"][part]
    cart_as_data.shipping_method.cash_on_delivery_fee = shipping_price["cash_on_delivery_fee"][part]
    return cart_as_data


def add_shipping_to_default_split_order(channel, attribute_value, len_parts, is_shipping_added, idx):
    """
    Dodaje koszt wysyłki do domyślnego zamówienia częściowego. Jeżeli domyślny atrybut nie został skonfigurowany, to koszt wysyłki zostanie dodany do ostatniego zamówienia częściowego.
    """
    if (
        channel.split_order_shipping_cost_mechanism
        == SplitOrderShippingCostMechanism.ATTRIBUTE_BASED_SHIPPING_COST_ASSIGNMENT
        and str(channel.default_attr_split_shipping).lower() == str(attribute_value).lower()
    ):
        is_shipping_added = True
        idx = 0

    elif not is_shipping_added and idx == len_parts - 1:
        idx = 0
        is_shipping_added = True
    else:
        idx = 1
    return idx, is_shipping_added


def add_payment_fee_default_split_order(channel, attribute_value, len_parts, is_fee_added, idx):
    """
    Dodaje koszt wysyłki do domyślnego zamówienia częściowego. Jeżeli domyślny atrybut nie został skonfigurowany, to koszt wysyłki zostanie dodany do ostatniego zamówienia częściowego.
    """
    if (
        channel.split_order_payment_fee_mechanism
        == SplitOrderPaymentFeeMechanism.ATTRIBUTE_BASED_PAYMENT_FEE_ASSIGNMENT
        and str(channel.default_attr_split_payment_fee).lower() == str(attribute_value).lower()
    ):
        is_fee_added = True
        idx = 0

    elif not is_fee_added and idx == len_parts - 1:
        idx = 0
        is_fee_added = True
    else:
        idx = 1
    return idx, is_fee_added


def split_orders_by_attribute(
    cart_as_data: "CheckoutData",
    channel: "Channel",
    items_to_split,
    len_parts,
    customer,
    request,
    original_order=None,
    original_cart=None,
):
    split_orders_pretty_id = []
    is_shipping_added = False
    is_fee_added = False
    first_order = None
    shipping_price = prepare_shipping_method_for_split_order(cart_as_data, parts=len_parts, channel=channel)
    fee_price = prepare_payment_fee_for_split_order(cart_as_data, parts=len_parts, channel=channel)

    if SplitOrderLink.objects.filter(original_cart=original_cart).exists():
        raise BadRequest("Order was already split")

    if channel.split_order_mechanism == SplitOrderMechanism.MODIFY_AND_CREATE:
        original_cart.cart_status = CartStatus.CLOSED
        original_cart.save()

    has_none_group = any(av is None or av == "None" for (av, _fi) in items_to_split.keys())
    for idx, ((attribute_value, feature_idx), items) in enumerate(items_to_split.items()):
        attribute_value = None if attribute_value == "None" else attribute_value
        gratis_items = []
        if (has_none_group and attribute_value is None) or (not has_none_group and idx == 0):
            gratis_items = [item for item in cart_as_data.cart.items if item.is_gratis]
        idx_shipping, is_shipping_added = add_shipping_to_default_split_order(
            channel, attribute_value, len_parts, is_shipping_added, idx
        )
        idx_fee, is_fee_added = add_payment_fee_default_split_order(
            channel, attribute_value, len_parts, is_fee_added, idx
        )
        cart_as_data = change_shipping_method_for_split_order(
            cart_as_data, shipping_price, part=idx_shipping, parts=len_parts
        )
        all_skus_split_order = [each["product__real_product__sku"] for each in items]

        split_order_link = SplitOrderLink(
            original_cart=original_cart if original_cart else None,
            original_order=original_order if original_order else None,
            split_by_attribute_value=str(attribute_value) if attribute_value else None,
            split_by_feature_idx=str(feature_idx),
            split_order_mechanism=channel.split_order_mechanism,
            channel=channel,
        )
        split_order_link.save()
        payment_method = _settling_payment_method(cart_as_data.payment_method)
        if payment_method is None:
            raise BadRequest("Cannot split an order without a payment method.")
        pm_data = {k: v for k, v in asdict(payment_method).items() if k in PaymentData.Schema().fields}
        sm_data = {k: v for k, v in asdict(cart_as_data.shipping_method).items() if k in ShippingData.Schema().fields}
        body = CartRequest(
            cart=None,
            addresses=AddressData(
                shipping_address=cart_as_data.addresses.shipping_address,
                billing_address=cart_as_data.addresses.billing_address,
                validation_status=ValidationStatus.INVALID,
            ),
            payment_method=PaymentData.Schema().load(pm_data),
            shipping_method=ShippingData.Schema().load(sm_data),
            language_code=cart_as_data.language_code,
            currency_code=cart_as_data.currency_code,
            country_code=cart_as_data.country_code,
            requested_delivery_date=None,
            custom_order_id=None,
            split_order=False,
            split_by_feature_idx=str(feature_idx),
            split_by_attr_idx=str(attribute_value) if attribute_value else None,
            original_order_id=str(original_order.order_id) if original_order else None,
            comment=cart_as_data.comment,
            affiliate_code=cart_as_data.affiliate_code,
            customer_ip=cart_as_data.customer_ip,
        )
        split_order_data = SplitOrderData(
            validated_items=[
                item for item in cart_as_data.cart.items if item.sku in all_skus_split_order and not item.is_gratis
            ]
            + gratis_items,
            all_validated_discount_data=cart_as_data.cart.discounts,
            cart_weight=cart_as_data.cart.total_weight,
            validated_shipping=cart_as_data.shipping_method,
            fee_price=Decimal(fee_price["fee_price"][idx_fee]),
        )

        def process_and_create_order(
            channel,
            body,
            customer,
            split_order_data,
            request,
            process_payment: bool = True,
            is_first_order_iteration: bool = True,
        ):
            geo_country = request.headers.get(HEADER_COUNTRY, None)

            processed_data, messages = process_cart(
                channel,
                body,
                customer=customer,
                geo_country=geo_country,
                split_order_data=split_order_data,
                cart=original_cart,
            )

            split_cart = Cart.create(processed_data, channel, customer=customer)
            split_cart.save()
            split_order_link.split_cart = split_cart
            split_order_link.save()
            split_cart.save()

            OrderValidation.validate(split_cart.cart_id, processed_data)
            oc = original_cart if is_first_order_iteration else None
            split_order, redirect, payment_error = Order.create(
                processed_data,
                split_cart,
                channel,
                customer=customer,
                request=request,
                process_payment=is_first_order_iteration,
                original_cart=oc,
            )
            split_orders_pretty_id.append(split_order.pretty_id)
            split_order_link.split_order = split_order
            split_order_link.save()
            split_cart.cart_status = split_order.cart.cart_status
            split_cart.save()
            return split_order, redirect, payment_error

        if idx == 0:
            match channel.split_order_mechanism:
                case SplitOrderMechanism.MODIFY_AND_CREATE:
                    if (
                        SplitOrderLink.objects.filter(original_cart=original_cart)
                        .exclude(pk=split_order_link.pk)
                        .exists()
                    ):
                        raise BadRequest("Order was already split")

                    first_order = process_and_create_order(channel, body, customer, split_order_data, request)

        else:
            split_order_link.original_order = first_order[0] if not original_order else original_order
            split_order_link.save()

            process_and_create_order(
                channel,
                body,
                customer,
                split_order_data,
                request,
                process_payment=False,
                is_first_order_iteration=False,
            )

    logger_process.add_log_param("split_orders_id", split_orders_pretty_id)
    logger_process.info("Order was successfully split")
    return split_orders_pretty_id, first_order
