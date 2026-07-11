# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import field
from datetime import date
from decimal import Decimal
from typing import ClassVar, Literal

import marshmallow.fields
import marshmallow.validate
from marshmallow import Schema, pre_load
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.domain.dto.address import AddressData
from django_checkout.domain.dto.discount import DiscountData, ValidatedDiscountData
from django_checkout.domain.dto.item import ItemData, ValidatedItemData
from django_checkout.domain.dto.payment import PaymentData, ValidatedPaymentData
from django_checkout.domain.dto.shipping import ShippingData, ValidatedShippingData
from django_checkout.enums import ValidationStatus
from django_checkout.utils import anonymize


def _wrap_legacy_payment_method_dict(data):
    """Pre-load helper: accept both legacy single-dict and new list-of-dicts payment_method.

    Legacy:  {"payment_method": {"code": "payu"}}      -> wrapped into list
    Multi:   {"payment_method": [{"code": "voucher"}, {"code": "payu"}]}  -> unchanged
    """
    pm = data.get("payment_method") if isinstance(data, dict) else None
    if pm is not None and isinstance(pm, dict):
        return {**data, "payment_method": [pm]}
    return data


def _first_payment_method(payment_methods):
    """Return first entry of a payment_method list, or None if empty/None."""
    if payment_methods:
        return payment_methods[0]
    return None


@add_schema
@dataclass
class CartData:
    items: list[ItemData | ValidatedItemData] | None
    discount_amount: Decimal | None
    discounts: list[DiscountData | ValidatedDiscountData] | None
    base_total_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    base_netto_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total_price: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    validation_status: ValidationStatus | None = field(metadata=dict(by_value=True))
    total_netto_price: Decimal | None = None
    tax_amount: Decimal | None = None
    # Schema-drift tolerant: accept any shape (List[dict] current, List[str] / dict legacy
    available_gratis_rules: list | None = field(
        default=None,
        metadata={"marshmallow_field": marshmallow.fields.Raw(allow_none=True, load_default=None)},
    )
    # Legacy zombie field (lived 2026-02-26 → 2026-02-27, commits 4b01278 / 96576c3).
    gratis_rules: list | None = field(
        default=None,
        metadata={"marshmallow_field": marshmallow.fields.Raw(allow_none=True, load_default=None)},
    )
    is_gratis_available: bool | None = None
    allowed_guest_checkout: bool | None = None
    amount_required_for_nearest_gratis_rule: Decimal | None = None
    amount_missing_for_nearest_gratis_rule: Decimal | None = None
    total_weight: Decimal | None = field(default=None, metadata={"validate": marshmallow.validate.Range(min=0)})
    discount_amount_netto: Decimal | None = None
    customs_threshold_scenario: str | None = None
    voucher_amount_applied: Decimal | None = None
    Schema: ClassVar[type[Schema]] = Schema

    @staticmethod
    def factory():
        return CartData(
            items=[],
            discount_amount=None,
            discounts=[],
            base_total_price=None,
            base_netto_price=None,
            total_price=None,
            validation_status=None,
            is_gratis_available=False,
            allowed_guest_checkout=True,
        )

    def get_valid_items(self):
        return [item for item in self.items if hasattr(item, "status") and item.status == ValidationStatus.VALID]

    def is_clear_discounts(self):
        if self.discounts:
            return any(discount.clear_discounts for discount in self.discounts)
        else:
            return False


@add_schema
@dataclass
class CartRequest:
    cart: CartData | None
    addresses: AddressData | None
    payment_method: list[PaymentData | ValidatedPaymentData] | None
    shipping_method: ShippingData | ValidatedShippingData | None
    language_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    currency_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=3)})
    country_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    custom_order_id: str | None = field(metadata={"validate": marshmallow.validate.Length(max=80)})
    requested_delivery_date: date | None
    split_order: bool | None
    split_by_feature_idx: str | None
    split_by_attr_idx: str | None
    original_order_id: str | None
    comment: str | None
    affiliate_code: str | None
    customer_ip: str | None
    Schema: ClassVar[type[Schema]] = Schema

    def __post_init__(self, *args, **kwargs):
        if self.country_code is not None:
            self.country_code = self.country_code.upper()

    @pre_load
    def _normalize_payment_method(self, data, **kwargs):
        return _wrap_legacy_payment_method_dict(data)

    @property
    def primary_payment_method(self):
        return _first_payment_method(self.payment_method)

    @staticmethod
    def factory():
        return CartRequest(
            cart=CartData.factory(),
            addresses=None,
            payment_method=None,
            shipping_method=None,
            language_code=None,
            currency_code=None,
            country_code=None,
            split_by_feature_idx=None,
            split_by_attr_idx=None,
            original_order_id=None,
            comment=None,
        )


@add_schema
@dataclass
class CheckoutData:
    cart: CartData
    addresses: AddressData | None
    payment_method: list[ValidatedPaymentData] | None
    shipping_method: ValidatedShippingData | None
    total_to_min_order_price: str | None
    base_total: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    total: Decimal = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    fee_price: Decimal = field(
        metadata={"validate": marshmallow.validate.Range(min=0), "load_default": Decimal(0), "required": False}
    )
    fee_tax_price: Decimal = field(
        metadata={"validate": marshmallow.validate.Range(min=0), "load_default": Decimal(0), "required": False}
    )
    fee_tax_rate: Decimal = field(
        metadata={"validate": marshmallow.validate.Range(min=0), "load_default": Decimal(0), "required": False}
    )
    total_tax: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    language_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    currency_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=3)})
    country_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    requested_delivery_date: date | None
    need_full_address: bool | None
    custom_order_id: str | None = field(metadata={"validate": marshmallow.validate.Length(max=80)})
    split_order: bool | None
    split_by_feature_idx: str | None
    split_by_attr_idx: str | None
    original_order_id: str | None
    comment: str | None
    affiliate_code: str | None
    customer_ip: str | None
    validation_status: ValidationStatus = field(metadata=dict(by_value=True))
    Schema: ClassVar[type[Schema]] = Schema

    @pre_load
    def _normalize_payment_method(self, data, **kwargs):
        return _wrap_legacy_payment_method_dict(data)

    @property
    def primary_payment_method(self):
        return _first_payment_method(self.payment_method)

    def anonymize(self) -> bool:
        save = False
        if self.addresses is not None:
            if self.addresses.billing_address is not None:
                self.addresses.billing_address.email = anonymize(self.addresses.billing_address.email, "email")
                if hasattr(self.addresses.billing_address, "firstname"):
                    self.addresses.billing_address.firstname = anonymize(
                        self.addresses.billing_address.firstname, "str"
                    )
                if hasattr(self.addresses.billing_address, "lastname"):
                    self.addresses.billing_address.lastname = anonymize(self.addresses.billing_address.lastname, "str")
                save = True
            if self.addresses.shipping_address is not None:
                self.addresses.shipping_address.email = anonymize(self.addresses.shipping_address.email, "email")
                if hasattr(self.addresses.shipping_address, "firstname"):
                    self.addresses.shipping_address.firstname = anonymize(
                        self.addresses.shipping_address.firstname, "str"
                    )
                if hasattr(self.addresses.shipping_address, "lastname"):
                    self.addresses.shipping_address.lastname = anonymize(
                        self.addresses.shipping_address.lastname, "str"
                    )
                save = True
        return save

    @staticmethod
    def factory():
        return CheckoutData(
            cart=CartData.factory(),
            addresses=None,
            payment_method=None,
            total_to_min_order_price=None,
            shipping_method=None,
            base_total=Decimal(0),
            total=Decimal(0),
            fee_price=Decimal(0),
            fee_tax_price=Decimal(0),
            fee_tax_rate=Decimal(0),
            total_tax=Decimal(0),
            language_code=None,
            need_full_address=True,
            currency_code=None,
            country_code=None,
            custom_order_id=None,
            requested_delivery_date=None,
            split_order=False,
            split_by_feature_idx=None,
            split_by_attr_idx=None,
            original_order_id=None,
            comment=None,
            affiliate_code=None,
            customer_ip=None,
            validation_status=ValidationStatus.INVALID,
        )


@add_schema
@dataclass
class CartResponse(CheckoutData):
    cart_id: str
    cart_status: str
    free_shipping: bool
    amount_required_for_free_shipping: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    amount_missing_for_free_shipping: Decimal | None = field(metadata={"validate": marshmallow.validate.Range(min=0)})
    amount_required_for_free_shipping_above_modifier: Decimal | None = field(
        metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    amount_missing_for_free_shipping_above_modifier: Decimal | None = field(
        metadata={"validate": marshmallow.validate.Range(min=0)}
    )
    can_be_split: bool | None = None
    tax_rates: dict | None = None
    min_order_price: Decimal | None = None


@add_schema
@dataclass
class CartMerge:
    operation_type: Literal["override", "add"]
    Schema: ClassVar[type[Schema]] = Schema
