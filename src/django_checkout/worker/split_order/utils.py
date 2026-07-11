# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from decimal import Decimal

from django_checkout.domain.dto.cart import CartData, ValidationStatus


def generate_split_order_cart(split_order_data, get_item_list_status):
    """
    Podczas tworzenia zesplitowanych koszyków tworzy instancje CartData na bazie oryginalnego koszyka
    """

    validated_items = split_order_data.validated_items
    validation_status = (
        ValidationStatus.INVALID if len(validated_items) == 0 else get_item_list_status([*validated_items])
    )
    cart = CartData(
        items=validated_items,
        discounts=split_order_data.all_validated_discount_data,
        total_weight=sum([item.total_weight for item in validated_items if item.total_weight]),
        available_gratis_rules=None,
        base_total_price=sum([item.base_total_price for item in validated_items]),
        # odejmuje tutaj discount, bo dla splitowanych orderów nie procesuje discountow, tylko je przenosi z oryginalnego orderu
        total_price=sum([item.total_price - item.discount_amount for item in validated_items if item]),
        discount_amount=sum([item.discount_amount for item in validated_items if item.discount_amount]),
        discount_amount_netto=sum(
            [item.discount_amount_netto for item in validated_items if item.discount_amount_netto]
        ),
        validation_status=validation_status,
        total_netto_price=sum(
            [item.total_price_netto - item.discount_amount_netto for item in validated_items if item]
        ),
        base_netto_price=sum(
            [Decimal(round(item.base_total_price / (1 + (item.tax_rate or 0)), 2)) for item in validated_items if item]
        ),
    )
    cart.tax_amount = round(abs(cart.total_price - cart.total_netto_price), 2)
    return cart, "", split_order_data.cart_weight
