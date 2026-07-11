# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import field
from typing import ClassVar

import marshmallow.validate
from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.enums import ValidationStatus

_EMAIL_VALIDATOR = marshmallow.validate.Email()


def _email_or_blank(value: str) -> str:
    """Validate email format, but tolerate a blank string.

    Shipping addresses are stored without an email (the v2 shipping schema has no
    email field), so `cart_body` legitimately holds `email: ""`. A strict Email()
    validator made `Cart.as_data` fail to reload any cart with a shipping address.
    Real emails are still validated at the Pydantic API boundary.
    """
    if value:
        _EMAIL_VALIDATOR(value)
    return value


@add_schema
@dataclass
class SimplifiedAddress:
    email: str = field(metadata={"validate": _email_or_blank})
    country_code: str | None = field(metadata={"validate": marshmallow.validate.Length(equal=2)})

    def __post_init__(self, *args, **kwargs):
        if self.country_code is not None:
            self.country_code = self.country_code.upper()


@add_schema
@dataclass
class Address(SimplifiedAddress):
    firstname: str
    lastname: str
    country_code: str = field(metadata={"validate": marshmallow.validate.Length(equal=2)})
    city: str
    postcode: str
    street: str
    dialling_code: str
    telephone: str
    company: str | None
    address_id: int | None = None
    external_id: str | None = None
    Schema: ClassVar[type[Schema]] = Schema

    def __post_init__(self, *args, **kwargs):
        if self.country_code is not None:
            self.country_code = self.country_code.upper()
        if self.dialling_code is not None:
            if not self.dialling_code.startswith("+"):
                self.dialling_code = f"+{self.dialling_code}"

    def pretty_address(self):
        return (
            f"{self.firstname} {self.lastname}, \n"
            f"{self.street}, \n"
            f"{self.city} {self.postcode}, \n"
            f" {self.country_code} \n"
            f"T: {self.dialling_code} {self.telephone}, \n"
            f"{self.city} {self.postcode}, \n"
        )


@add_schema
@dataclass
class BillingAddress(Address):
    requested_invoice: bool = False
    # Nullable to match the v2 API schemas (BillingAddressInput / response), which type
    # this as `bool | None`. The v2 write path stores `null` ("VAT not validated"); a
    # non-nullable domain field made `Cart.as_data` fail to reload any billing-address cart.
    vat_validation_status: bool | None = False
    invoice_requested_status: bool = False
    tax_id: str | None = None


@add_schema
@dataclass
class AddressData:
    shipping_address: SimplifiedAddress | Address | None
    billing_address: SimplifiedAddress | BillingAddress | None
    validation_status: ValidationStatus | None = field(metadata=dict(by_value=True))
    Schema: ClassVar[type[Schema]] = Schema
