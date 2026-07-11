# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from dataclasses import field
from typing import ClassVar

from marshmallow import Schema
from marshmallow_dataclass import add_schema, dataclass

from django_checkout.enums import ItemStatus


@add_schema
@dataclass
class PaymentData:
    code: str
    name: str | None
    card: str | None
    bank_id: int | None
    country_code: str | None
    authorization_token: str | None
    continue_url: str | None
    save_card: bool | None
    pay_code: str | None = None
    Schema: ClassVar[type[Schema]] = Schema

    def add_order_id_to_continue_url(self, order_id: str) -> str | None:
        if self.continue_url:
            return self.continue_url.replace("<order_id>", order_id)
        else:
            return None


@add_schema
@dataclass
class ValidatedPaymentData(PaymentData):
    is_cash_on_delivery: bool | None = None
    status: ItemStatus = field(metadata=dict(by_value=True), default=ItemStatus.INVALID)
