# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from enum import unique

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _


@unique
class LineType(TextChoices):
    PRODUCT = "product", _("Product")
    SHIPPING = "shipping", _("Shipping")


@unique
class FeeType(TextChoices):
    WITHOUT_FEE = "without_fee", _("Without Fee")
    FLAT_FEE = "flat_fee", _("Flat Fee")
    PERCENTAGE_FEE = "percentage_fee", _("Percentage Fee")


@unique
class OrderStatus(TextChoices):
    NEW = "new", _("New")
    UNPAID = "unpaid", _("Unpaid")
    CONFIRMED = "confirmed", _("Confirmed")
    HOLDED = "holded", _("On hold")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "complete", _("Completed")
    RETURNED = "returned", _("Returned")
    CANCELED = "canceled", _("Canceled")


@unique
class CartStatus(TextChoices):
    NEW = "new", _("New")  # Cart is new and open to changes
    CONFIRMED = "confirmed", _("Confirmed")  # Cart is closed and there is an order related to it
    CLOSED = "closed", _("Closed")  # Cart is closed and there is no order related to it


@unique
class ItemStatus(TextChoices):
    # OK
    VALID = "valid", _("Valid")  # OK
    # NOK
    OUT_OF_STOCK = "out_of_stock", _("Unavailable")  # Item is out of stock
    INVALID = "invalid", _("Invalid")  # Some unrecoverable exception occured
    # CHANGED
    CHANGED_PRICE_AND_QTY = "changed_price_and_qty", _("Price changed and there is not enough in stock")
    CHANGED_PRICE = "changed_price", _("Price changed")  # Item price has changed since last valuation
    CHANGED_QTY = "changed_qty", _("Not enough in stock")  # There is not enough of the item in stock


@unique
class ValidationStatus(TextChoices):
    """
    Validation rules:
    If there is an invalid element or item inside an object, the whole object is invalid
    If there are no invalid elements or items but there is at least one changed, the whole object is changed
    If there are no invalid or changed elements or items, then the whole object is valid

    For item status refrence lookup ItemStatus enum
    """

    VALID = "valid", _("Valid")  # Valid
    INVALID = "invalid", _("Invalid")  # Missing, incomplete or invalid data
    CHANGED = "changed", _("Changed")  # Valid but changed compared to what client sent (ex. some item's price changed)


@unique
class PaymentIntentStatus(TextChoices):
    NEW = "new", _("New")
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Pending")
    COMPLETE = "complete", _("Complete")
    CANCELLED = "cancelled", _("Cancelled")
    ERROR = "error", _("Error")


@unique
class DiscountType(TextChoices):
    PERCENT = "percent", _("Discount by percentage")
    PRICE = "price", _("Discount by fixed amount")
    SHIP = "shipping", _("Free shipping")


@unique
class ExternalServiceType(TextChoices):
    BASELINKER = "bl", _("Baselinker")
    MAGENTO = "mag", _("Magento")


class AssociationChoices(TextChoices):
    SKU = "sku", _("Sku - in 'idx' field")
    CATEGORY_IDX = "category_idx", _("Category idx - in 'idx' field")
    ATTRIBUTE_IDX = "attribute_idx", _("Attribute idx - in 'idx' field")
    ATTRIBUTE_VALUE = "attribute_value", _("Attribute value - in 'idx' feature_idx, in 'value' attr value")
    PRODUCT_CLASS = "product_class", _("Product class (integer!)  - in 'idx' field")
    FEATURE_SET_IDX = "feature_set_idx", _("Feature set idx - in 'idx' field")


class AuthenticationState(TextChoices):
    ALL = "all", _("All")
    GUEST = "guest", _("Guest")
    LOGGED = "logged", _("Logged")


class PriceType(TextChoices):
    FIXED = "fixed", _("Fixed")
    MATRIX = "matrix", _("Matrix")


@unique
class SplitOrderShippingCostMechanism(TextChoices):
    ATTRIBUTE_BASED_SHIPPING_COST_ASSIGNMENT = (
        "attribute_based_shipping_cost_assignment",
        _(
            "Assigning the delivery cost to the split order with the specified attribute provided in the 'default_attr_split_shipping' field."
        ),
    )
    SHIPPING_COST_EQUALLY_DIVIDED = (
        "shipping_cost_equally_divided",
        _(
            "Divides the shipping cost equally among all orders. Any potential rounding discrepancies will be added to the first one in the sequence."
        ),
    )


@unique
class SplitOrderPaymentFeeMechanism(TextChoices):
    ATTRIBUTE_BASED_PAYMENT_FEE_ASSIGNMENT = (
        "attribute_based_payment_fee_assignment",
        _(
            "Assigning the payment fee to the split order with the specified attribute provided in the 'default_attr_payment_fee' field."
        ),
    )
    PAYMENT_FEE_EQUALLY_DIVIDED = (
        "payment_fee_equally_divided",
        _(
            "Divides the payment fee equally among all orders. Any potential rounding discrepancies will be added to the first one in the sequence."
        ),
    )
