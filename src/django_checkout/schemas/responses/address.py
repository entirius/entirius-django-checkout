# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from pydantic import BaseModel, Field


class CheckoutAddressResponse(BaseModel):
    """Canonical 13-field shape — mirrors accounts BaseAddressResponse."""

    address_id: int | None = Field(None, description="Accounts address PK", examples=[42])
    firstname: str | None = Field(None, description="First name", examples=["Jan"])
    lastname: str | None = Field(None, description="Last name", examples=["Kowalski"])
    street: str | None = Field(None, description="Street address", examples=["Marszalkowska 89"])
    city: str | None = Field(None, description="City", examples=["Warszawa"])
    postcode: str | None = Field(None, description="Postal code", examples=["00-001"])
    country_code: str | None = Field(None, description="ISO 2-letter country code", examples=["PL"])
    telephone: str | None = Field(None, description="Phone number", examples=["500500500"])
    dialling_code: str | None = Field(None, description="International dialling code", examples=["+48"])
    company: str | None = Field(None, description="Company name")
    is_company: bool = Field(False, description="Whether this is a company address", examples=[False])
    tax_id: str | None = Field(None, description="Tax identification number")
    external_id: str | None = Field(None, description="External system ID")
    source: str | None = Field(None, description="'address_book' or 'manual'")

    @classmethod
    def from_domain(cls, addr) -> "CheckoutAddressResponse | None":
        if addr is None:
            return None
        if isinstance(addr, dict):
            d = addr
        else:
            d = {
                "firstname": getattr(addr, "firstname", None),
                "lastname": getattr(addr, "lastname", None),
                "street": getattr(addr, "street", None),
                "city": getattr(addr, "city", None),
                "postcode": getattr(addr, "postcode", None),
                "country_code": getattr(addr, "country_code", None),
                "telephone": getattr(addr, "telephone", None),
                "dialling_code": getattr(addr, "dialling_code", None),
                "company": getattr(addr, "company", None),
                "is_company": getattr(addr, "is_company", False),
                "tax_id": getattr(addr, "tax_id", None),
                "address_id": getattr(addr, "address_id", None),
                "external_id": getattr(addr, "external_id", None),
                "source": getattr(addr, "source", None),
            }
        return cls(**{k: v for k, v in d.items() if k in cls.model_fields})


class BillingAddressResponse(CheckoutAddressResponse):
    """Billing address extends with email, invoice, VAT."""

    email: str | None = Field(None, description="Billing email for order confirmation", examples=["jan@example.com"])
    requested_invoice: bool = Field(False, description="Customer requested invoice")
    vat_validation_status: bool | None = Field(None, description="VAT number validation result")

    @classmethod
    def from_domain(cls, addr) -> "BillingAddressResponse | None":
        if addr is None:
            return None
        if isinstance(addr, dict):
            d = addr
        else:
            d = {
                "firstname": getattr(addr, "firstname", None),
                "lastname": getattr(addr, "lastname", None),
                "street": getattr(addr, "street", None),
                "city": getattr(addr, "city", None),
                "postcode": getattr(addr, "postcode", None),
                "country_code": getattr(addr, "country_code", None),
                "telephone": getattr(addr, "telephone", None),
                "dialling_code": getattr(addr, "dialling_code", None),
                "company": getattr(addr, "company", None),
                "is_company": getattr(addr, "is_company", False),
                "tax_id": getattr(addr, "tax_id", None),
                "address_id": getattr(addr, "address_id", None),
                "external_id": getattr(addr, "external_id", None),
                "source": getattr(addr, "source", None),
                "email": getattr(addr, "email", None),
                "requested_invoice": getattr(addr, "requested_invoice", False),
                "vat_validation_status": getattr(addr, "vat_validation_status", None),
            }
        return cls(**{k: v for k, v in d.items() if k in cls.model_fields})


class AddressesResponse(BaseModel):
    billing_address: BillingAddressResponse | None = None
    shipping_address: CheckoutAddressResponse | None = None

    @classmethod
    def from_domain(cls, addr_data) -> "AddressesResponse | None":
        if addr_data is None:
            return None
        return cls(
            billing_address=BillingAddressResponse.from_domain(getattr(addr_data, "billing_address", None)),
            shipping_address=CheckoutAddressResponse.from_domain(getattr(addr_data, "shipping_address", None)),
        )
