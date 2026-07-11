# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from marshmallow_dataclass import add_schema, dataclass


@add_schema
@dataclass
class PayPalPayment:
    token: str
    PayerID: str = None
