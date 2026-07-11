# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.services.customer import CustomerService


def admin_customer_delete(email: str, logger) -> tuple[bool, list[str], list[str], list[str], list[str]]:
    customer_service = CustomerService()
    customer_service.set_logger(logger)
    return customer_service.anonymize_customer(email)
