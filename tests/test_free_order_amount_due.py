# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""free_order may only close a cart with nothing left to pay.

free_order confirms the order and marks its payment complete without any payment. A cart with an
amount due is invalid with ``free_order_amount_due``, ``Order.create`` refuses on its own (it
resolves the methods from the stored cart body by code), and the payment method list does not
offer free_order while something is due.

The carts here have no products (that needs PIM price seeding); the amount due comes from a
flat fee on the payment method, which ``process_cart`` adds like any other.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from django_checkout.domain.validators.payment import FREE_ORDER_AMOUNT_DUE_CODE, free_order_with_amount_due
from django_checkout.enums import FeeType, PaymentProvider, ValidationStatus
from django_checkout.models import Order, PaymentMethod
from django_checkout.services import cart_service

FREE = SimpleNamespace(is_free_order=True)
GATEWAY = SimpleNamespace(is_free_order=False)
ADDRESS = {
    "email": "buyer@example.com",
    "firstname": "Test",
    "lastname": "Buyer",
    "country_code": "PL",
    "city": "City",
    "postcode": "00-001",
    "street": "Street 1",
    "dialling_code": "+48",
    "telephone": "500000000",
}


@pytest.mark.parametrize(
    "methods,amount_due,expected",
    [
        ([FREE], Decimal("0"), False),
        ([FREE], Decimal("0.00"), False),
        ([FREE], Decimal("0.01"), True),
        ([FREE], Decimal("199.99"), True),
        ([GATEWAY], Decimal("199.99"), False),
        ([GATEWAY, FREE], Decimal("10"), True),
        ([], Decimal("10"), False),
        (None, Decimal("10"), False),
        ([FREE], None, False),
    ],
)
def test_free_order_needs_nothing_left_to_pay(methods, amount_due, expected):
    assert free_order_with_amount_due(methods, amount_due) is expected


def _free_order_cart(channel, default_country, default_currency, fee: float):
    method = PaymentMethod.objects.create(
        channel=channel,
        code="free",
        provider=PaymentProvider.FREE_ORDER,
        name_t9n={"en": "Free"},
        fee_type=FeeType.FLAT_FEE,
        fee_value=fee,
    )
    method.countries.add(default_country)
    method.currencies.add(default_currency)
    cart, _ = cart_service.create_cart(
        channel=channel, items=[], currency_code="EUR", language_code="en", country_code="PL"
    )
    cart_service.patch_addresses(channel, cart.cart_id, billing=ADDRESS, shipping=ADDRESS)
    cart, messages = cart_service.patch_payment(channel, cart.cart_id, code="free")
    return cart, messages


def _codes(messages):
    return [getattr(message, "code", None) for message in messages]


@pytest.mark.django_db
def test_cart_with_an_amount_due_cannot_settle_by_free_order(channel, default_country, default_currency):
    cart, messages = _free_order_cart(channel, default_country, default_currency, fee=10)

    assert cart.as_data.total == Decimal("10.00")
    assert FREE_ORDER_AMOUNT_DUE_CODE in _codes(messages)
    assert cart.validation_status == ValidationStatus.INVALID


@pytest.mark.django_db
def test_cart_with_nothing_to_pay_may_settle_by_free_order(channel, default_country, default_currency):
    _, messages = _free_order_cart(channel, default_country, default_currency, fee=0)

    assert FREE_ORDER_AMOUNT_DUE_CODE not in _codes(messages)


@pytest.mark.django_db
def test_order_create_refuses_free_order_with_an_amount_due_on_its_own(channel, default_country, default_currency):
    """Even if a stored cart body claims VALID, the order must not be created and confirmed."""
    cart, _ = _free_order_cart(channel, default_country, default_currency, fee=10)
    data = cart.as_data
    data.validation_status = ValidationStatus.VALID
    cart = cart.replace(data)
    cart.save()

    with pytest.raises(Exception, match="free order"):
        Order.create(cart.as_data, cart, channel, request=None)

    assert not Order.objects.filter(cart=cart).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("fee,offered", [(10, False), (0, True)], ids=["amount-due", "nothing-to-pay"])
def test_payment_method_list_offers_free_order_only_with_nothing_to_pay(
    channel, default_country, default_currency, fee, offered
):
    cart, _ = _free_order_cart(channel, default_country, default_currency, fee=fee)

    codes = list(cart.available_payment_methods(False, None).values_list("code", flat=True))

    assert ("free" in codes) is offered
