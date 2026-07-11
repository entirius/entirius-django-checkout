# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.


from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django_utils.api.responses import ErrorInfo

from django_checkout import settings
from django_checkout.domain.dto.address import AddressData
from django_checkout.enums import ValidationStatus

SHIPPING_ADDRESS = "shipping"
BILLING_ADDRESS = "billing"


def _validate_email(email: str, affected_field: str) -> ErrorInfo | None:
    try:
        validate_email(email)
    except ValidationError:
        return ErrorInfo(
            code="invalid_email_characters",
            message="Email address contains invalid characters.",
            affected_values=[email],
            affected_field=affected_field,
        )
    return None


def validate_addresses(
    data: AddressData, checkout_data: AddressData = None
) -> tuple[AddressData | None, list[ErrorInfo]]:
    errors: list[ErrorInfo] = []

    def assign_address_by_type(address_object: AddressData, address_type: str) -> AddressData:
        if address_type == SHIPPING_ADDRESS:
            if data is not None and data.shipping_address is not None:
                address_object.shipping_address = data.shipping_address
            elif checkout_data is not None and checkout_data.shipping_address is not None:
                address_object.shipping_address = checkout_data.shipping_address
            return address_object
        elif address_type == BILLING_ADDRESS:
            if data is not None and data.billing_address is not None:
                address_object.billing_address = data.billing_address
            elif checkout_data is not None and checkout_data.billing_address is not None:
                address_object.billing_address = checkout_data.billing_address
            return address_object
        else:
            raise ValueError("Wrong Address type.")

    def assaign_addresses(address_object: AddressData) -> AddressData:
        address_object = assign_address_by_type(address_object, SHIPPING_ADDRESS)
        address_object = assign_address_by_type(address_object, BILLING_ADDRESS)
        if address_object.shipping_address is not None and address_object.billing_address is not None:
            address_object.validation_status = ValidationStatus.VALID
        return address_object

    address = AddressData(billing_address=None, shipping_address=None, validation_status=ValidationStatus.INVALID)

    if data is None:
        if checkout_data is None:
            return None, errors
        else:
            result = assaign_addresses(address)
    else:
        result = assaign_addresses(address)

    if settings.VALIDATE_ADDRESS_EMAIL:
        if result.shipping_address and result.shipping_address.email:
            if error := _validate_email(result.shipping_address.email, "addresses.shipping_address.email"):
                errors.append(error)
        if result.billing_address and result.billing_address.email:
            if error := _validate_email(result.billing_address.email, "addresses.billing_address.email"):
                errors.append(error)
        if errors:
            result.validation_status = ValidationStatus.INVALID

    return result, errors
