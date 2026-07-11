# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Q
from django_pim.models import Product
from django_utils.api.responses import ErrorInfo

from django_checkout.domain.dto.address import Address, SimplifiedAddress
from django_checkout.domain.dto.item import SkuQuantityData
from django_checkout.domain.dto.shipping import ShippingData, ValidatedShippingData
from django_checkout.domain.shipping import get_delivery_matrix_prices
from django_checkout.enums import ItemStatus
from django_checkout.models import ShippingMethod, ShippingOption
from django_checkout.worker.product_virtual import check_all_virtual_products

if TYPE_CHECKING:
    from django_checkout.models.channel import Channel


def validate_shipping_method(
    channel: "Channel",
    shipping_method: ShippingMethod,
    shipping_data: ShippingData,
    shipping_option,
    address: Address | None,
    country,
    lang,
    sku_qty_list: list[SkuQuantityData],
    split_order_data=None,
) -> (ValidatedShippingData | None, str | None):
    def generate_dummy_shipping_data(shipping):
        return ValidatedShippingData(
            code=shipping.code,
            country_code=shipping.country_code,
            name=shipping.code,
            base_unit_price=Decimal("0.00"),
            base_total_price=Decimal("0.00"),
            discount_amount=Decimal("0.00"),
            unit_price=Decimal("0.00"),
            total_price=Decimal("0.00"),
            cash_on_delivery_fee=None,
            free_delivery_above=None,
            free_delivery_above_modifier=None,
            status=ItemStatus.INVALID,
            delivery_point=shipping.delivery_point if hasattr(shipping, "delivery_point") else None,
            tax_rate=Decimal("0.00"),
            unit_price_netto=Decimal("0.00"),
            total_price_netto=Decimal("0.00"),
        )

    def build_validated_shipping_data(shipping: ShippingData):
        if address is None and shipping.country_code != "ALL":
            return None, "You need shipping address to add shipping. "

        if country is None and shipping.country_code is None:
            return None, "You need country code inside shipping address or inside request to add shipping."

        if shipping_option is not None:
            cod_extra = shipping_option.cash_on_delivery_fee
            language = lang.lower() if lang is not None else ""

            try:
                (
                    free_delivery_items,
                    free_delivery_modifier_items,
                    free_delivery_above,
                    free_delivery_above_modifier,
                    new_shipping_price,
                    new_shipping_price_modifier,
                ) = get_delivery_matrix_prices(shipping_option, sku_qty_list, channel)

                if new_shipping_price is None and new_shipping_price_modifier is None:
                    return generate_dummy_shipping_data(shipping), "No shipping price found. "

                # if only modifier products are in cart do not apply free delivery above
                new_shipping_price = new_shipping_price if free_delivery_items else 0
                new_shipping_price_modifier = new_shipping_price_modifier if free_delivery_modifier_items else 0
                unit_price = new_shipping_price + new_shipping_price_modifier
                base_unit_price = unit_price
            except ValueError as e:
                return generate_dummy_shipping_data(shipping), f"Error while getting delivery prices: {e}. "

            unit_price_netto = Decimal(unit_price / (1 + Decimal(shipping_option.tax_rate or 0))).quantize(
                Decimal("0.01")
            )

            discount_amount = Decimal(0.00)
            if unit_price <= base_unit_price:
                discount_amount = base_unit_price - unit_price

            return (
                ValidatedShippingData(
                    code=shipping_option.method.code,
                    country_code=shipping_option.country_code,
                    name=shipping_option.method.name_t9n.get(language, shipping_option.method.code),
                    base_unit_price=base_unit_price,
                    base_total_price=base_unit_price,
                    discount_amount=discount_amount,
                    unit_price=unit_price,
                    total_price=unit_price,
                    cash_on_delivery_fee=cod_extra,
                    free_delivery_above=free_delivery_above,
                    free_delivery_above_modifier=free_delivery_above_modifier,
                    status=ItemStatus.VALID,
                    delivery_point=shipping_data.delivery_point if hasattr(shipping_data, "delivery_point") else None,
                    tax_rate=shipping_option.tax_rate,
                    tax_amount=unit_price - unit_price_netto,
                    unit_price_netto=unit_price_netto,
                    total_price_netto=unit_price_netto,
                    normal_price=new_shipping_price,
                    modifier_price=new_shipping_price_modifier,
                    free_delivery_items=free_delivery_items,
                    free_delivery_modifier_items=free_delivery_modifier_items,
                ),
                "",
            )
        else:
            return generate_dummy_shipping_data(shipping), "Added dummy Shipping method. "

    if split_order_data:
        return split_order_data.validated_shipping, ""
    elif shipping_method is None:
        return None, ""
    else:
        return build_validated_shipping_data(shipping_method)


def get_shipping_method_option(
    shipping_method: ShippingData,
    address: Address | SimplifiedAddress | None,
    currency,
    channel: "Channel",
    sku_qty_list: list[SkuQuantityData],
    checkout_shipping_method: ShippingData = None,
    customer=None,
):

    msg = []
    shipping_item: ShippingOption = None
    shipping = shipping_method or checkout_shipping_method

    is_all_virtual_product = check_all_virtual_products(sku_qty_list, channel, currency)
    need_full_address = not is_all_virtual_product

    if isinstance(address, Address) and not is_all_virtual_product:
        if shipping is None:
            return None, None, [], need_full_address
        shipping_item = ShippingOption.objects.get_item(
            shipping.code if shipping is not None else None,
            address.country_code if address is not None or not hasattr(shipping, "country_code") else "ALL",
            currency,
            channel,
            sku_qty_list,
            is_logged=True if customer else False,
        )
        return shipping_item, shipping, msg, need_full_address

    elif isinstance(address, SimplifiedAddress):
        shipping_items = ShippingOption.objects.get_available_items(
            address.country_code if address is not None or not hasattr(shipping, "country_code") else "ALL",
            currency,
            channel,
            sku_qty_list,
            is_logged=True if customer else False,
        )
        try:
            from django_pim.models import KindOfProductEnum

            kind_of_product_query = ~Q(real_product__kind_of_product=KindOfProductEnum.ProductVirtual)
        except ImportError:
            kind_of_product_query = Q()

        not_virtual = Product.objects.filter(
            Q(shop__idx=channel.idx), kind_of_product_query & Q(real_product__sku__in=sku_qty_list)
        )

        if not_virtual.exists():
            error = ErrorInfo(
                code="non_virtual_products_in_cart_with_simplified_address",
                message="Non-virtual products in the cart. Need to specify full address.",
                affected_values=[],
                affected_field="cart.items.sku",
            )
            msg.append(error)
            return None, None, [error], need_full_address

        if len(shipping_items) == 0:
            error = ErrorInfo(
                code="shipping_method_not_exist",
                message="Shipping method virtual does not exist",
                affected_values=[],
                affected_field="cart.shipping_method.code",
            )
            msg.append(error)

        elif len(shipping_items) == 1:
            shipping_item = shipping_items.first()

        elif len(shipping_items) > 1 and not shipping:
            error = ErrorInfo(
                code="multiple_virtual_shipping_methods_in_channel",
                message="Multiple virtual shipping methods in channel. Need to specify one.",
                affected_values=[],
                affected_field="cart.shipping_method.code",
            )
            msg.append(error)
            return None, None, [error], need_full_address

        elif len(shipping_items) > 1 and shipping:
            shipping_item = shipping_items.filter(method__code=shipping.code).first()

        else:
            return None, None, ["There is no virtual shipping method for this channel. "], need_full_address

        if not shipping_item:
            error = ErrorInfo(
                code="shipping_method_not_exist",
                message="Shipping method does not exist in channel.",
                affected_values=[],
                affected_field="cart.shipping_method.code",
            )
            msg.append(error)
            return None, None, [error], need_full_address

        return shipping_item, shipping_item.method, msg, need_full_address

    else:
        return None, None, [], need_full_address
