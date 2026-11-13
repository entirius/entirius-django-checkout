# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Shared fixtures and builders of the order e-mail tests; fixtures are imported by the test modules."""

import pytest

from django_checkout.enums import OrderMailKind, OrderStatus, PaymentProvider
from django_checkout.models import Cart, Order, OrderMail, PaymentMethod, SplitOrderLink
from django_checkout.worker import order_mail_tasks

pytest.importorskip("django_email.service.checkout.order_confirmation")  # the extra `email` with the order e-mails

PROVIDERS = {
    "transfer": PaymentProvider.TRANSFER,
    "cod": PaymentProvider.COD,
    "free": PaymentProvider.FREE_ORDER,
    "payu": PaymentProvider.PAYU,
    "voucher": PaymentProvider.VOUCHER,
}
BUYER = "buyer@example.com"


def body(code: str = "transfer", **overrides) -> dict:
    """An order_body as checkout stores it: amounts are strings."""
    return {
        "cart": {
            "items": [
                {"sku": "ENT-LAMP-01", "name": "Desk lamp", "quantity": "2", "unit_price": "61.50",
                 "total_price": "123.00", "is_gratis": False, "sub_items": None},
            ],
            "total_price": "123.00",
            "discount_amount": "0.00",
            "voucher_amount_applied": None,
            "discounts": [],
        },
        "addresses": {
            "billing_address": {"firstname": "Ada", "lastname": "Buyer", "street": "Main 1", "postcode": "00-001",
                                "city": "Town", "country_code": "PL", "email": BUYER, "tax_id": None},
            "shipping_address": {"firstname": "Ada", "lastname": "Buyer", "street": "Main 1", "postcode": "00-001",
                                 "city": "Town", "country_code": "PL", "email": BUYER},
        },
        "payment_method": [{"code": code, "name": code.title()}],
        "shipping_method": {"code": "courier", "name": "Courier", "total_price": "10.00", "delivery_point": None,
                            "cash_on_delivery_fee": None},
        "fee_price": "0",
        "total": "133.00",
        "total_tax": "23.00",
        "currency_code": "PLN",
        "language_code": "en",
        "comment": None,
        **overrides,
    }  # fmt: skip


def method(channel, code: str) -> PaymentMethod:
    found = PaymentMethod.objects.filter(channel=channel, code=code).first()
    return found or PaymentMethod.objects.create(
        channel=channel, code=code, provider=PROVIDERS[code], name_t9n={"en": code.title()}
    )


def make_order(channel, code: str = "transfer", status: str = OrderStatus.UNPAID, **body_overrides) -> Order:
    method(channel, code)
    return Order.objects.create(
        channel=channel,
        order_status=status,
        order_body=body(code, **body_overrides),
        billing_email=BUYER,
        shipping_email=BUYER,
    )


def split_purchase(channel, parts: list[Order]) -> None:
    """Link the parts as the split worker does: one original cart, a split cart per part, the lead first."""
    original = Cart.objects.create(channel=channel, cart_body={})
    for part in parts:
        split_cart = Cart.objects.create(channel=channel, cart_body={})
        SplitOrderLink.objects.create(
            channel=channel, original_cart=original, split_cart=split_cart, split_order=part,
            original_order=parts[0] if part != parts[0] else None, split_by_feature_idx="warehouse",
            split_by_attribute_value=str(part.pk),
        )  # fmt: skip


def mails(order: Order, kind: str = OrderMailKind.CONFIRMATION) -> list[OrderMail]:
    return list(OrderMail.objects.filter(order=order, kind=kind))


def set_status(order: Order, status: str) -> Order:
    order.refresh_from_db()
    order.order_status = status
    order.save()
    return order


@pytest.fixture
def enqueued(monkeypatch) -> list[tuple]:
    """Records the task calls instead of reaching a broker: (mail id, was_paid)."""
    calls = []
    monkeypatch.setattr(order_mail_tasks.send_order_mail, "delay", lambda *args: calls.append(args))
    return calls


@pytest.fixture
def email_channel(channel):
    """The django_email channel of the shop: same idx as the checkout channel."""
    from django_email.models import Channel as EmailChannel

    return EmailChannel.objects.create(idx=channel.idx, label="Test shop", from_email="shop@example.com")


@pytest.fixture
def on_commit(django_capture_on_commit_callbacks):
    """Runs the after-commit hooks of the block, and the ones they register in turn."""
    return lambda: django_capture_on_commit_callbacks(execute=True)
