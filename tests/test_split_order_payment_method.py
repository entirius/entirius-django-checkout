# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Split order picks the settling payment method off the payment_method list.

`payment_method` is a list — a cart may carry a voucher next to a gateway method — so a
split part, which is created against a single method, has to resolve the list first.
Passing the list itself to `asdict()` raises `TypeError: asdict() should be called on
dataclass instances`, and picking entry [0] bills the child order to the voucher.
"""

from dataclasses import asdict

import pytest

from django_checkout.domain.dto.payment import PaymentData, ValidatedPaymentData
from django_checkout.worker.split_order.split_orders import _settling_payment_method


def _pm(code, cls=ValidatedPaymentData):
    return cls(
        code=code,
        name=code,
        card=None,
        bank_id=None,
        country_code="PL",
        authorization_token=None,
        continue_url=None,
        save_card=None,
    )


def test_plain_gateway_list_is_unwrapped():
    payu = _pm("payu")

    assert _settling_payment_method([payu]) is payu


def test_voucher_listed_first_but_the_gateway_settles():
    """The voucher is listed before the gateway when its balance is debited first.
    Copying entry [0] into the child order would bill the split part to the voucher."""
    voucher, payu = _pm("voucher"), _pm("payu")

    assert _settling_payment_method([voucher, payu]) is payu


def test_order_within_the_list_does_not_matter():
    voucher, payu = _pm("voucher"), _pm("payu")

    assert _settling_payment_method([payu, voucher]) is payu


def test_voucher_only_cart_falls_back_to_the_voucher():
    voucher = _pm("voucher")

    assert _settling_payment_method([voucher]) is voucher


def test_none_entries_are_skipped():
    payu = _pm("payu")

    assert _settling_payment_method([None, payu]) is payu


@pytest.mark.parametrize("empty", [[], None])
def test_nothing_to_settle_returns_none(empty):
    assert _settling_payment_method(empty) is None


def test_result_is_a_dataclass_so_asdict_works():
    """The actual crash: asdict() was handed the list itself."""
    picked = _settling_payment_method([_pm("voucher"), _pm("payu")])

    data = {k: v for k, v in asdict(picked).items() if k in PaymentData.Schema().fields}

    assert data["code"] == "payu"


def test_legacy_unvalidated_payment_data_is_accepted():
    """A cart body written before the validation pass carries PaymentData, not
    ValidatedPaymentData - both are dataclasses and both must resolve."""
    payu = _pm("payu", cls=PaymentData)

    assert _settling_payment_method([payu]) is payu
