# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""PaymentIntent per-method amounts (multi-method: voucher + gateway).

Pure tests cover the amount rule (`_intent_amount` / `_is_voucher_method`);
the DB test validates the schema (amount/currency columns + the
(order, code) constraint that allows two intents per order).
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from django_checkout.enums import PaymentProvider
from django_checkout.models.order import Order, _intent_amount, _is_voucher_method
from django_checkout.models.payment_intent import PaymentIntent


def _data(total: str, voucher_applied: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        total=Decimal(total),
        cart=SimpleNamespace(voucher_amount_applied=Decimal(voucher_applied) if voucher_applied else None),
    )


class TestIntentAmountRule:
    def test_voucher_plus_gateway_split(self):
        data = _data(total="149.10", voucher_applied="50.90")
        assert _intent_amount(True, data) == Decimal("50.90")
        assert _intent_amount(False, data) == Decimal("149.10")
        # the two always reconstruct the base total
        assert _intent_amount(True, data) + _intent_amount(False, data) == Decimal("200.00")

    def test_no_voucher_gateway_covers_total(self):
        data = _data(total="200.00", voucher_applied=None)
        assert _intent_amount(False, data) == Decimal("200.00")
        assert _intent_amount(True, data) == Decimal("0")

    def test_voucher_only_covers_base_total(self):
        data = _data(total="0", voucher_applied="200.00")
        assert _intent_amount(True, data) == Decimal("200.00")
        assert _intent_amount(False, data) == Decimal("0")


class TestVoucherDiscriminator:
    def test_provider_wins_over_code(self):
        method = SimpleNamespace(provider=PaymentProvider.VOUCHER)
        assert _is_voucher_method(method, "bon-podarunkowy") is True

    def test_gateway_provider_is_not_voucher(self):
        method = SimpleNamespace(provider=PaymentProvider.PAYU)
        assert _is_voucher_method(method, "payu") is False

    def test_null_method_falls_back_to_code(self):
        assert _is_voucher_method(None, "voucher") is True
        assert _is_voucher_method(None, "payu") is False


@pytest.mark.django_db
class TestPaymentIntentColumns:
    def test_two_intents_with_amounts_roundtrip(self, channel):
        order = Order.objects.create(channel=channel, order_body={})
        PaymentIntent.objects.create(order=order, code="payu", amount=Decimal("149.10"), currency="PLN")
        PaymentIntent.objects.create(order=order, code="voucher", amount=Decimal("50.90"), currency="PLN")

        rows = {i.code: i for i in PaymentIntent.objects.filter(order=order)}
        assert rows["payu"].amount == Decimal("149.10")
        assert rows["voucher"].amount == Decimal("50.90")
        assert rows["voucher"].currency == "PLN"

    def test_legacy_row_allows_null_amount(self, channel):
        order = Order.objects.create(channel=channel, order_body={})
        intent = PaymentIntent.objects.create(order=order, code="payu")
        intent.refresh_from_db()
        assert intent.amount is None
        assert intent.currency is None
