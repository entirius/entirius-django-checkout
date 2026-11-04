# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""The payment bridge tells the mobile app the status of the payment the client was sent to make.

It took the order's first PaymentIntent. With ``[voucher, gateway]`` that is the voucher's, debited at order time:
the app read ``complete`` while the gateway payment was still pending.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from django.test import RequestFactory
from django.utils import timezone

from django_checkout import settings as checkout_settings
from django_checkout.enums import OrderStatus, PaymentIntentStatus, PaymentProvider
from django_checkout.models import Order, PaymentIntent, PaymentMethod, PaymentRedirect
from django_checkout.views.payment_redirect import payment_redirect_bridge

COMPLETE, PENDING = PaymentIntentStatus.COMPLETE, PaymentIntentStatus.PENDING


@pytest.fixture
def order(channel, monkeypatch):
    monkeypatch.setattr(checkout_settings, "PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES", ["exp"])
    return Order.objects.create(channel=channel, order_body={}, order_status=OrderStatus.UNPAID)


def _intent(order, code, provider, status):
    method = PaymentMethod.objects.create(channel=order.channel, code=code, provider=provider) if provider else None
    return PaymentIntent.objects.create(order=order, code=code, method=method, payment_status=status, amount="10.00")


def _reported_status(order) -> str:
    expires_at = timezone.now() + timezone.timedelta(minutes=30)
    redirect = PaymentRedirect.objects.create(order=order, target_url="exp://app.example/done", expires_at=expires_at)
    response = payment_redirect_bridge(RequestFactory().get("/"), "1", order.channel.idx, redirect.token)
    assert response.status_code == 302
    (status,) = parse_qs(urlparse(response["Location"]).query)["payment_status"]
    return status


@pytest.mark.django_db
class TestTheSettlingPaymentIsReported:
    def test_a_gateway_after_a_voucher(self, order):
        _intent(order, "voucher", PaymentProvider.VOUCHER, COMPLETE)
        _intent(order, "payu", PaymentProvider.PAYU, PENDING)

        assert _reported_status(order) == PENDING

    def test_a_gateway_before_a_voucher(self, order):
        _intent(order, "payu", PaymentProvider.PAYU, PENDING)
        _intent(order, "voucher", PaymentProvider.VOUCHER, COMPLETE)

        assert _reported_status(order) == PENDING

    def test_a_voucher_method_named_otherwise_is_still_a_voucher(self, order):
        _intent(order, "gift-card", PaymentProvider.VOUCHER, COMPLETE)
        _intent(order, "payu", PaymentProvider.PAYU, PENDING)

        assert _reported_status(order) == PENDING

    def test_a_removed_voucher_method_is_told_by_its_code(self, order):
        _intent(order, "voucher", None, COMPLETE)
        _intent(order, "payu", PaymentProvider.PAYU, PENDING)

        assert _reported_status(order) == PENDING

    def test_full_voucher_coverage_reports_the_voucher(self, order):
        """``Order.create`` makes no gateway intent when the vouchers cover the order."""
        _intent(order, "voucher", PaymentProvider.VOUCHER, COMPLETE)

        assert _reported_status(order) == COMPLETE

    def test_an_order_without_a_payment_is_unknown(self, order):
        assert _reported_status(order) == "unknown"
