# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Order validation refuses a valid item priced 0, and nothing else priced 0.

``OrderValidation`` compared ``str(i.total_price) == 0``, a string against an int, so the check never fired and an
item the price list priced at 0 went into an order. Two zeros are legitimate and must still pass: a gift is free
when the shop sets ``GRATIS_MECHANISM = 2`` and ``GRATIS_PRICE = 0``, and an item that is not valid carries 0 from
``ValidatedItemData.invalid_factory`` while its own status names the reason.
"""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django_utils.api.exceptions import Forbidden

from django_checkout import settings as checkout_settings
from django_checkout.domain.dto.cart import CheckoutData
from django_checkout.domain.dto.item import ValidatedItemData
from django_checkout.domain.validators.order import OrderValidation
from django_checkout.enums import ItemStatus
from django_checkout.worker import discount_worker
from tests.test_gratis_stock import (  # noqa: F401 - fixtures
    GIFT_SKU,
    TRIGGER_SKU,
    _claim_the_gift,
    _stock,
    catalog,
    coffee_in_stock,
    gratis_code,
    gratis_rule,
    pim_channel,
    priced_catalog,
    supplier,
)


def ready(checkout: CheckoutData) -> CheckoutData:
    """The cart with addresses, shipping and payment chosen, so only its items decide."""
    return replace(checkout, addresses=object(), shipping_method=object(), payment_method=object())


def with_items(*items: ValidatedItemData) -> SimpleNamespace:
    """What ``OrderValidation`` reads of a processed cart: its items and the three choices, all made."""
    return SimpleNamespace(
        cart=SimpleNamespace(items=list(items)), addresses=object(), shipping_method=object(), payment_method=object()
    )


def item(sku: str, total: str, status: ItemStatus = ItemStatus.VALID) -> ValidatedItemData:
    line = ValidatedItemData.invalid_factory(sku=sku, status=status, is_gratis=False, quantity=1)
    line.unit_price = line.total_price = Decimal(total)
    return line


def refusal(checkout) -> str:
    with pytest.raises(Forbidden) as refused:
        OrderValidation.validate("cart-1", checkout)
    return refused.value.status


def test_a_valid_item_priced_zero_is_refused():
    checkout = with_items(item(TRIGGER_SKU, "123.00"), item("ENT-FREE-1", "0.00"))

    assert refusal(checkout) == "UNPROCESSABLE_ENTITY sku: ENT-FREE-1"


def test_an_out_of_stock_item_keeps_its_own_reason():
    """``invalid_factory`` prices it 0; the reason is the stock, not the price."""
    checkout = with_items(item(TRIGGER_SKU, "0.00", status=ItemStatus.OUT_OF_STOCK))

    assert refusal(checkout) == f"OUT_OF_STOCK sku: {TRIGGER_SKU}"


@pytest.mark.django_db
@pytest.mark.usefixtures("gratis_code", "priced_catalog", "coffee_in_stock")
def test_a_free_gift_from_the_cart_passes(monkeypatch, channel, supplier):  # noqa: F811
    monkeypatch.setattr(checkout_settings, "GRATIS_MECHANISM", 2)
    monkeypatch.setattr(checkout_settings, "GRATIS_PRICE", 0)
    monkeypatch.setattr(discount_worker, "GRATIS_MECHANISM", 2)
    monkeypatch.setattr(discount_worker, "GRATIS_PRICE", 0)
    _stock(channel, supplier, GIFT_SKU, quantity=5)
    checkout = _claim_the_gift(channel).as_data

    assert [(line.sku, line.total_price) for line in checkout.cart.items if line.is_gratis] == [(GIFT_SKU, 0)]
    assert OrderValidation.validate("cart-1", ready(checkout)) is True
