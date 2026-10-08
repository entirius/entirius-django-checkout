# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json

from django.core.handlers.wsgi import WSGIRequest
from django.views.decorators.csrf import csrf_exempt
from django_utils.api.decorators import require_http_method
from django_utils.api.exceptions import BadRequest, NotFound
from django_utils.api.responses import Response
from process_logger import ProcessLogger

from django_checkout.services.customer import CustomerService
from django_checkout.utils.api.decorators import admin_view
from django_checkout.utils.api_keys import erase_channel

logger = ProcessLogger("CHECKOUT_ADMIN_VIEW", module="django_checkout")


@admin_view
@csrf_exempt
@require_http_method("DELETE")
def admin_customer_delete(request: WSGIRequest, *args, **kwargs):
    """
    Endpoint to delete a customer email from order.
    Only the authenticated admin can delete the customer data.
    Firstname and lastname will be anonymized.
    """

    try:
        data = json.loads(request.body)
        email = data.get("email")
        if not email:
            raise BadRequest(message="Email is required")
    except json.JSONDecodeError:
        raise BadRequest(message="Invalid JSON in request body")

    customer_service = CustomerService()
    customer_service.set_logger(logger)
    success, orders_idxs, carts_idxs, orders_fail, carts_fail = customer_service.anonymize_customer(
        email, erase_channel(request)
    )

    if not success:
        raise BadRequest(
            data={
                "deleted": False,
                "orders": orders_idxs,
                "carts": carts_idxs,
                "orders_fail": orders_fail,
                "carts_fail": carts_fail,
            },
            message="Customer data in orders anonymization failed",
        )

    if not orders_idxs and not carts_idxs:
        raise NotFound(data={"deleted": False}, message="No data found for this customer")

    return Response(
        data={"deleted": True, "orders": orders_idxs, "carts": carts_idxs}, message="Customer data in orders anonymized"
    )
