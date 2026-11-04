# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django_checkout.services.customer import CustomerService, ErasureBlocked

__all__ = ["ErasureBlocked", "admin_customer_delete"]


def admin_customer_delete(email: str, logger) -> tuple[bool, list[str], list[str], list[str], list[str]]:
    """Anonymise the orders and carts of ``email`` in every channel (docs/api/checkout.md, "Customer erasure").

    Raises ``ErasureBlocked`` (``.orders``: id, number and status of each) and anonymises nothing while the customer
    has an order the shop has not fulfilled yet.
    """
    customer_service = CustomerService()
    customer_service.set_logger(logger)
    return customer_service.anonymize_customer(email)
