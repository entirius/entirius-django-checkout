# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_utils.api.exceptions import Forbidden


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

            if str(i.total_price) == 0:
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

        if processed_data.addresses is None:
            flag = True
            status = "ADDRESSES_NOT_SELECTED"
            message = f"There is no addresses in this cart {cart}"

        if flag:
            raise Forbidden(message=message, status=status, data=data)

        return True
