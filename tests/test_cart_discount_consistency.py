# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Bug B: automatic discount consistency between read and write paths.

Automatic discount rules (``automatic_applications=True``) are only discovered
inside ``process_cart`` — the write path. The v2 GET views now build via
``build_v2_cart_response_fresh``, which recomputes read-only (no DB write) so a
plain read reflects the same discounts/totals a write would.

These tests assert the fix's guarantees:
1. the fresh builder recomputes (auto-rule discovery runs on GET),
2. it does NOT persist (no write on read),
3. it falls back to the stored snapshot if recompute fails (a read must not 500).

A full priced-cart discount assertion needs PIM product/price seeding and is
covered by the BDD suite / make-dev smoke, not here.
"""

from unittest.mock import patch

import pytest

from django_checkout.services import cart_service
from django_checkout.services.cart_response_builder import build_v2_cart_response_fresh


@pytest.fixture
def cart(channel):
    record, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    return record


def test_fresh_builder_recomputes(cart):
    with patch.object(cart_service, "recompute_checkout_data", wraps=cart_service.recompute_checkout_data) as spy:
        build_v2_cart_response_fresh(cart)
    spy.assert_called_once_with(cart)


def test_fresh_builder_does_not_persist(cart):
    from django_checkout.models import Cart

    before = Cart.objects.get(pk=cart.pk).cart_body
    build_v2_cart_response_fresh(cart)
    after = Cart.objects.get(pk=cart.pk).cart_body
    assert before == after  # read-only: GET must not write the cart


def test_fresh_builder_falls_back_on_recompute_error(cart):
    with patch.object(cart_service, "recompute_checkout_data", side_effect=RuntimeError("boom")):
        result = build_v2_cart_response_fresh(cart)
    assert isinstance(result, dict)  # fell back to stored snapshot instead of 500
    assert result.get("cart_id") == str(cart.cart_id)
