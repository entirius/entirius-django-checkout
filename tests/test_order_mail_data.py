# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The data objects of the order e-mails, read from a free-form ``order_body``; the e-mail language."""

import sys
import types

import pytest

from django_checkout.enums import OrderStatus
from django_checkout.models import Order
from django_checkout.services import order_mail_catalog
from django_checkout.services.order_mail_data import build_canceled_data, build_confirmation_data
from django_checkout.worker.order_mail_tasks import mail_language
from tests.order_mail_support import body, email_channel, make_order, method, split_purchase

__all__ = ["email_channel"]  # fixtures

INSTRUCTIONS = "<p>Account 00 0000</p>"


@pytest.mark.django_db
class TestConfirmationData:
    def test_every_block_from_a_full_body(self, channel):
        method(channel, "transfer").__class__.objects.filter(code="transfer").update(
            description_t9n={"en": INSTRUCTIONS}
        )
        order = make_order(channel, "transfer", comment="Ring twice")

        data = build_confirmation_data(order, "en")

        assert data.currency == "PLN" and data.customer_name == "Ada Buyer" and data.comment == "Ring twice"
        assert [part.order_number for part in data.parts] == [order.pretty_id]
        item = data.parts[0].items[0]
        assert (item.name, item.sku, item.quantity, item.unit_price, item.total_price) == (
            "Desk lamp", "ENT-LAMP-01", "2", "61.50", "123.00",
        )  # fmt: skip
        assert item.variant_line is None  # no matrix in the module's tests
        assert (data.delivery.method_name, data.delivery.price) == ("Courier", "10.00")
        assert data.delivery.address_lines == ["Ada Buyer", "Main 1", "00-001 Town", "PL"]
        assert (data.totals.items, data.totals.delivery, data.totals.total, data.totals.tax) == (
            "123.00", "10.00", "133.00", "23.00",
        )  # fmt: skip
        assert data.billing_lines == ["Ada Buyer", "Main 1", "00-001 Town", "PL"]

    def test_transfer_carries_instructions_and_amount_due(self, channel):
        method(channel, "transfer").__class__.objects.filter(code="transfer").update(
            description_t9n={"en": INSTRUCTIONS}
        )

        payment = build_confirmation_data(make_order(channel, "transfer"), "en").payment

        assert (payment.kind, payment.is_paid) == ("transfer", False)
        assert (payment.instructions_html, payment.amount_due, payment.method_name) == (
            INSTRUCTIONS,
            "133.00",
            "Transfer",
        )

    def test_cash_on_delivery_carries_amount_due_and_fee(self, channel):
        shipping = {**body()["shipping_method"], "cash_on_delivery_fee": "5.00"}
        order = make_order(channel, "cod", status=OrderStatus.CONFIRMED, shipping_method=shipping, fee_price="2.00")

        payment = build_confirmation_data(order, "en").payment

        assert (payment.kind, payment.is_paid, payment.amount_due, payment.fee) == ("cod", False, "133.00", "7.00")
        assert payment.instructions_html is None

    @pytest.mark.parametrize("status", [OrderStatus.CONFIRMED, OrderStatus.IN_PROGRESS])
    def test_paid_gateway_order(self, channel, status):
        payment = build_confirmation_data(make_order(channel, "payu", status=status), "en").payment

        assert (payment.kind, payment.is_paid, payment.amount_due) == ("online", True, None)

    def test_pickup_point_is_mapped_from_known_keys(self, channel):
        point = {"code": "WAW01", "name": "Locker WAW01", "street": "Long 5", "post_code": "00-950", "city": "Warsaw",
                 "opening_hours": "24/7"}  # fmt: skip
        order = make_order(channel, shipping_method={**body()["shipping_method"], "delivery_point": point})

        pickup = build_confirmation_data(order, "en").delivery.pickup_point

        assert (pickup.name, pickup.address_lines) == ("Locker WAW01", ["Long 5", "00-950 Warsaw"])

    def test_gifts_are_flagged(self, channel):
        cart = body()["cart"]
        gift = {"sku": "ENT-GIFT", "name": "Gift", "quantity": "1", "unit_price": "0.01", "total_price": "0.01",
                "is_gratis": True}  # fmt: skip
        order = make_order(channel, cart={**cart, "items": [*cart["items"], gift]})

        items = build_confirmation_data(order, "en").parts[0].items

        assert [item.is_gift for item in items] == [False, True]

    @pytest.mark.parametrize("raw", [{}, {"cart": "x", "total": "abc", "addresses": None, "shipping_method": []},
                                     {"cart": {"items": [{"sku": 1, "total_price": "NaN"}, "junk"]}, "fee_price": "1e999999"}])  # fmt: skip
    def test_an_empty_or_malformed_body_gives_a_valid_object(self, channel, raw):
        order = Order.objects.create(channel=channel, order_body=raw, order_status=OrderStatus.CONFIRMED)

        data = build_confirmation_data(order, "en")

        assert data.totals.total == "0.00" and data.delivery.pickup_point is None
        assert data.payment.kind == "other"

    def test_split_purchase_lists_every_part_and_sums_the_totals(self, channel):
        lead = make_order(channel, total="80.00", total_tax="10.00")
        second = make_order(channel, total="53.00", total_tax="13.00")
        split_purchase(channel, [lead, second])

        data = build_confirmation_data(second, "en")

        assert [part.order_number for part in data.parts] == [lead.pretty_id, second.pretty_id]
        assert sum(len(part.items) for part in data.parts) == 2
        assert (data.totals.total, data.totals.tax, data.totals.items) == ("133.00", "23.00", "246.00")

    def test_split_purchase_fee_is_the_same_in_payment_and_totals(self, channel):
        lead, second = make_order(channel, fee_price="5.00"), make_order(channel, fee_price="5.00")
        split_purchase(channel, [lead, second])

        data = build_confirmation_data(lead, "en")

        assert data.payment.fee == data.totals.payment_fee == "10.00"


@pytest.mark.django_db
def test_canceled_data_names_the_order_only(channel):
    order = make_order(channel, status=OrderStatus.CANCELED)

    data = build_canceled_data(order, True, "en")

    assert (data.order_number, data.total, data.was_paid, len(data.items)) == (order.pretty_id, "133.00", True, 1)


@pytest.mark.django_db
class TestLanguage:
    def test_the_body_language_wins(self, channel):
        assert mail_language(make_order(channel, language_code="pl")) == "pl"

    @pytest.mark.parametrize("requested", ["xx", None, 7])
    def test_an_unknown_language_falls_back_to_the_channel_default(self, channel, requested):
        assert channel.default_language.iso2 == "en"

        assert mail_language(make_order(channel, language_code=requested)) == "en"


class TestVariantLines:
    def test_nothing_without_matrix(self):
        assert order_mail_catalog.variant_lines(["ENT-LAMP-01"], "test-channel", "en") == {}

    def test_label_value_pairs_with_matrix(self, monkeypatch):
        rows = [("ENT-TEE", {"en": "Size"}, "M", None), ("ENT-TEE", {"en": "Colour"}, None, {"en": "Red"}),
                ("ENT-TEE", {"pl": "Wzór"}, "Kropki", None)]  # fmt: skip
        _fake_matrix(monkeypatch, rows)

        assert order_mail_catalog.variant_lines(["ENT-TEE"], "test-channel", "en") == {
            "ENT-TEE": "Size: M, Colour: Red"
        }


def _fake_matrix(monkeypatch, rows: list[tuple]) -> None:
    class Rows(list):
        def order_by(self, *fields):
            return self

    class Manager:
        def filter(self, **lookups):
            return types.SimpleNamespace(values_list=lambda *fields: Rows(rows))

    module = types.ModuleType("django_matrix.models")
    module.Attribute = types.SimpleNamespace(objects=Manager())
    monkeypatch.setitem(sys.modules, "django_matrix.models", module)
    monkeypatch.setattr(order_mail_catalog.apps, "is_installed", lambda label: True)
