# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_utils.api.exceptions import Forbidden
from django_utils.api.responses import ErrorInfo

VOUCHER_ON_SPLIT_ORDER = "voucher_not_supported_on_split_order"
# Shipping method types that deliver to a pickup point (django_deliverypoints), not to the shipping address.
POINT_METHOD_TYPES = ("inpost", "pickup_point")


class CartNotReady(Forbidden):
    """The cart cannot become an order yet: no addresses, shipping or payment method, no pickup point for a method that
    needs one, or an item out of stock."""


def lacks_delivery_point(cart_id, shipping) -> bool:
    """The cart's shipping method delivers to a pickup point (``POINT_METHOD_TYPES``) and the cart names none.

    The cart data carries the method's code, not its type, so the type comes from the method of the cart's channel.
    """
    code = getattr(shipping, "code", None)
    if not code or (getattr(shipping, "delivery_point", None) or {}).get("code"):
        return False
    # Imported here: the models import the domain package, which imports this module.
    from django_checkout.models import Cart, ShippingMethod

    return ShippingMethod.objects.filter(
        code=code,
        channel__in=Cart.objects.filter(cart_id=cart_id).values("channel"),
        method_type__in=POINT_METHOD_TYPES,
    ).exists()


def voucher_on_split_order_error(split_parts: int) -> ErrorInfo:
    """The cart's error for a voucher on a cart that splits into ``split_parts`` orders."""
    return ErrorInfo(
        code=VOUCHER_ON_SPLIT_ORDER,
        message=(
            f"This cart splits into {split_parts} orders and a voucher cannot pay for several orders. "
            "Remove the voucher, or the items that split the cart, to continue."
        ),
        affected_field="cart.payment_method",
        extra={"split_parts": split_parts},
    )


def refuse_voucher_on_split_order(messages: list) -> None:
    """Refuse the order when ``process_cart`` refused a voucher on a cart that splits.

    Reads the cart's verdict instead of detecting the voucher again: the voucher may be a payment entry or only
    an amount the voucher module took off the total. The split would create every part with the gateway alone,
    so the parts would add up to the full amount and the voucher keep its balance.
    """
    refusal = next((m for m in messages or [] if getattr(m, "code", None) == VOUCHER_ON_SPLIT_ORDER), None)
    if refusal is not None:
        raise CartNotReady(message=refusal.message, status=VOUCHER_ON_SPLIT_ORDER.upper(), data=refusal.extra)


class OrderValidation:
    @staticmethod
    def validate(cart, processed_data, *args, **kwargs):
        flag = False
        message = ""
        data = {}
        status = ""
        for i in processed_data.cart.items:
            if "out_of_stock" == str(i.status):
                flag = True
                status = f"OUT_OF_STOCK sku: {i.sku}"
                message = f"Not enought quantity for SKU {i.sku}"

            # A gift may be free (GRATIS_PRICE 0); an item that is not valid carries 0 from invalid_factory.
            if str(i.status) == "valid" and not getattr(i, "is_gratis", False) and i.total_price == 0:
                flag = True
                status = f"UNPROCESSABLE_ENTITY sku: {i.sku}"
                message = f"Price is 0 for SKU {i.sku}"

        if processed_data.payment_method is None:
            flag = True
            status = "PAYMENT_OPTION_NOT_SELECTED"
            message = f"There is no payment method in this cart {cart}"

        if processed_data.shipping_method is None:
            flag = True
            status = "SHIPPING_OPTION_NOT_SELECTED"
            message = f"There is no shipping method in this cart {cart}"
        elif lacks_delivery_point(cart, processed_data.shipping_method):
            flag = True
            status = "DELIVERY_POINT_NOT_SELECTED"
            message = f"The shipping method of this cart {cart} delivers to a pickup point and none is selected"

        if processed_data.addresses is None:
            flag = True
            status = "ADDRESSES_NOT_SELECTED"
            message = f"There is no addresses in this cart {cart}"

        if flag:
            raise CartNotReady(message=message, status=status, data=data)

        return True
