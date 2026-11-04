# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Refusals whose message checkout wrote for the API client."""

from django.core.exceptions import ObjectDoesNotExist


class ClientError(Exception):
    """A refusal whose message this module wrote for the API client.

    A view answers with ``exc.client_message``, never with ``str(exc)``: only these classes carry text meant for
    a response, so an exception from a lower layer cannot reach a client by being caught too broadly.
    """

    def __init__(self, client_message: str) -> None:
        super().__init__(client_message)
        self.client_message = client_message


class InvalidDiscountInput(ClientError, ValueError):
    """A modifier, target, inclusion or bulk action the discount service does not accept (view -> 400)."""


class DiscountObjectNotFound(ClientError, ObjectDoesNotExist):
    """A discount rule, code or filter missing or outside the channel (view -> 404)."""
