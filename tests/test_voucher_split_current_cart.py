# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The cart refuses a voucher on a split for the cart it is answering, not for the one saved before.

``process_cart`` asked whether the stored cart record splits: its items and ``split_order`` were those of the
previous save. A PATCH that added the item that splits the cart came back without
``voucher_not_supported_on_split_order``, and the PATCH that removed it still carried it; the order gate (v1 403,
v2 409) always saw the final cart, so the customer learnt it only there.

The split needs PIM features: the splitter here splits a cart that holds the mug and asked for a split.
"""

import json
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory

from django_checkout.domain.cart import process_cart
from django_checkout.domain.payment_provider.base_payment_provider import BasePaymentProvider
from django_checkout.services import cart_service
from django_checkout.views.cart import get_or_put_cart
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _stock,
    catalog,
    coffee_in_stock,
    pim_channel,
    priced_catalog,
    supplier,
)
from tests.test_voucher_rejection_reasons import voucher_method  # noqa: F401

SPLITTER = "django_checkout.domain.cart.check_that_order_can_be_split"
VOUCHER_ON_SPLIT_ORDER = "voucher_not_supported_on_split_order"
COFFEE = {"sku": TRIGGER_SKU, "quantity": 1}
MUG = {"sku": GIFT_SKU, "quantity": 1}
BUYER = {
    "email": "buyer@example.org",
    "firstname": "Jan",
    "lastname": "Kowalski",
    "country_code": "PL",
    "city": "Warsaw",
    "postcode": "00-001",
    "street": "Main 1",
    "dialling_code": "+48",
    "telephone": "123456789",
}


class _AttachedVoucherProvider(BasePaymentProvider):
    """The voucher module's provider, which ships outside this repository: the code is attached to the cart."""

    def get_payment_method_extension(self, cart=None):
        return {"applied_count": 1}


def _mug_splits_off(cart, channel, skip_user_declaration=False):
    """Two orders when the cart asks for a split and holds the mug, as a split by a PIM feature would give."""
    data = cart.as_data
    if data.split_order and GIFT_SKU in {item.sku for item in data.cart.items}:
        return {}, 2
    return None, None


@pytest.fixture
def shop(channel, priced_catalog, coffee_in_stock, supplier, voucher_method, monkeypatch):  # noqa: F811
    _stock(channel, supplier, GIFT_SKU, quantity=10)
    monkeypatch.setattr("django_checkout.models.payment_method.VoucherPaymentProvider", _AttachedVoucherProvider)
    with patch(SPLITTER, side_effect=_mug_splits_off):
        yield channel


def _cart_with_voucher(channel, items, split_order=True):
    record, _ = cart_service.create_cart(
        channel=channel, items=items, currency_code="EUR", language_code="en", country_code="PL"
    )
    record.cart_body["split_order"] = split_order
    record.save()
    cart_service.patch_addresses(channel, str(record.cart_id), BUYER, BUYER)
    record, messages = cart_service.patch_payment(channel, str(record.cart_id), code="voucher", pay_code="ENTGIFT1")
    return record, messages


def _refused(messages) -> bool:
    return VOUCHER_ON_SPLIT_ORDER in [getattr(message, "code", None) for message in messages]


@pytest.mark.django_db
class TestTheItemsOfThisRequest:
    def test_adding_the_item_that_splits_the_cart_is_answered_at_once(self, shop):
        record, messages = _cart_with_voucher(shop, [COFFEE])
        assert not _refused(messages)

        record, messages = cart_service.patch_items(shop, str(record.cart_id), [COFFEE, MUG])

        assert _refused(messages)
        assert record.as_data.validation_status == "invalid"

    def test_removing_it_lifts_the_refusal_at_once(self, shop):
        record, messages = _cart_with_voucher(shop, [COFFEE, MUG])
        assert _refused(messages)

        record, messages = cart_service.patch_items(shop, str(record.cart_id), [COFFEE])

        assert not _refused(messages)

    def test_a_save_that_changes_nothing_keeps_the_refusal(self, shop):
        record, _messages = _cart_with_voucher(shop, [COFFEE, MUG])

        _record, messages = cart_service.patch_addresses(shop, str(record.cart_id), BUYER, BUYER)

        assert _refused(messages)


@pytest.mark.django_db
def test_the_split_order_flag_of_this_request_counts(shop, api_key):
    record, messages = _cart_with_voucher(shop, [COFFEE, MUG], split_order=False)
    assert not _refused(messages)

    request = APIRequestFactory().patch(
        "/", json.dumps({"split_order": True}), content_type="application/json", HTTP_X_API_KEY=api_key.key
    )
    response = get_or_put_cart(request, channel_idx=shop.idx, cart_id=str(record.cart_id), version="1")

    assert response.status_code == 200
    errors = json.loads(response.content)["meta"]["errors"]
    assert VOUCHER_ON_SPLIT_ORDER in [error["code"] for error in errors]


@pytest.mark.django_db
def test_the_order_is_judged_on_the_cart_it_splits(shop):
    """The order splits the saved record (order_service, views/order): its gate must read the same cart."""
    record, _messages = _cart_with_voucher(shop, [COFFEE, MUG])
    seen = []

    def splitter(cart, channel, skip_user_declaration=False):
        seen.append(cart)
        return _mug_splits_off(cart, channel)

    with patch(SPLITTER, side_effect=splitter):
        _data, messages = process_cart(shop, record.as_data, cart=record, is_order_creation=True)

    assert seen == [record]
    assert _refused(messages)
