# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API: the order's e-mail log in the detail, the resend endpoint; erasure of the recipients.

Views are called directly via ``APIRequestFactory`` (the module has no ROOT_URLCONF for the API).
"""

import os

import pytest
from django.core.cache import cache
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.throttling import OrderMailResendThrottle
from django_checkout.api.v2.views.admin.order_views import AdminOrderDetailView, AdminOrderMailResendView
from django_checkout.enums import OrderMailKind, OrderMailStatus, OrderStatus
from django_checkout.models import OrderMail
from django_checkout.schemas.responses.order import OrderMailResponse
from tests.order_mail_support import BUYER, enqueued, make_order, on_commit, split_purchase

__all__ = ["enqueued", "on_commit"]  # fixtures

factory = APIRequestFactory()
CONFIRMATION, CANCELED = OrderMailKind.CONFIRMATION, OrderMailKind.CANCELED


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def _detail(user, order):
    request = factory.get("/")
    force_authenticate(request, user=user)
    return AdminOrderDetailView.as_view()(request, channel_idx=order.channel.idx, uid=order.pretty_id)


def _resend(user, order, kind):
    request = factory.post("/")
    if user is not None:
        force_authenticate(request, user=user)
    return AdminOrderMailResendView.as_view()(request, channel_idx=order.channel.idx, uid=order.pretty_id, kind=kind)


def _sent(order, kind=CONFIRMATION) -> OrderMail:
    return OrderMail.objects.create(order=order, kind=kind, status=OrderMailStatus.SENT, recipient=BUYER, attempts=1)


@pytest.mark.django_db
class TestDetail:
    def test_lists_the_order_mails(self, admin_user, channel):
        order = make_order(channel)
        _sent(order)

        mails = _detail(admin_user, order).data["mails"]

        assert [(mail["kind"], mail["status"], mail["order_numbers"]) for mail in mails] == [
            (CONFIRMATION, OrderMailStatus.SENT, [order.pretty_id])
        ]
        OrderMailResponse.model_validate(mails[0])

    def test_a_split_part_shows_the_lead_confirmation_with_both_numbers(self, admin_user, channel):
        lead, second = make_order(channel), make_order(channel)
        split_purchase(channel, [lead, second])
        _sent(lead)
        _sent(second, CANCELED)

        mails = _detail(admin_user, second).data["mails"]

        assert [(mail["kind"], mail["order_numbers"]) for mail in mails] == [
            (CONFIRMATION, [lead.pretty_id, second.pretty_id]),
            (CANCELED, [second.pretty_id]),
        ]


@pytest.mark.django_db
class TestResend:
    @pytest.mark.parametrize("kind", [CONFIRMATION, CANCELED])
    def test_puts_the_row_back_in_the_queue(self, admin_user, channel, enqueued, on_commit, kind):
        order = make_order(channel, status=OrderStatus.CANCELED)
        row = _sent(order, kind)

        with on_commit():
            response = _resend(admin_user, order, kind)

        assert response.status_code == 202, response.data
        row.refresh_from_db()
        assert (row.status, row.requested_by, row.attempts) == (OrderMailStatus.PENDING, admin_user, 1)
        assert enqueued == [(row.pk, None)]
        assert response.data["kind"] == kind

    def test_a_split_part_resends_the_lead_confirmation(self, admin_user, channel, enqueued, on_commit):
        lead, second = make_order(channel), make_order(channel)
        split_purchase(channel, [lead, second])
        row = _sent(lead)

        with on_commit():
            response = _resend(admin_user, second, CONFIRMATION)

        assert response.status_code == 202
        assert response.data["order_numbers"] == [lead.pretty_id, second.pretty_id]
        assert enqueued == [(row.pk, None)]

    def test_a_cancellation_is_never_created_by_a_resend(self, admin_user, channel, enqueued):
        order = make_order(channel, status=OrderStatus.CANCELED)

        response = _resend(admin_user, order, CANCELED)

        assert response.status_code == 400
        assert response.data["error"] == "VALIDATION_ERROR"
        assert not OrderMail.objects.exists() and enqueued == []

    def test_an_order_without_an_address_is_refused(self, admin_user, channel, enqueued):
        order = make_order(channel)
        order.billing_email = order.shipping_email = None
        order.save()

        response = _resend(admin_user, order, CONFIRMATION)

        assert response.status_code == 400 and enqueued == []

    def test_unknown_kind_is_not_found(self, admin_user, channel, enqueued):
        assert _resend(admin_user, make_order(channel), "shipped").status_code == 404

    def test_anonymous_is_refused(self, channel, enqueued):
        assert _resend(None, make_order(channel), CONFIRMATION).status_code == 401

    def test_customer_is_refused(self, regular_user, channel, enqueued):
        assert _resend(regular_user, make_order(channel), CONFIRMATION).status_code == 403

    def test_throttled(self, admin_user, channel, enqueued, monkeypatch):
        monkeypatch.setattr(OrderMailResendThrottle, "fallback_rate", "2/hour")
        order = make_order(channel)

        codes = [_resend(admin_user, order, CONFIRMATION).status_code for _ in range(3)]

        assert codes == [202, 202, 429]


@pytest.mark.parametrize(
    "configured,expected", [(None, "30/hour"), ("bad", "30/hour"), ("abc/xyz", "30/hour"), ("5/min", "5/min")]
)
def test_throttle_rate_falls_back_when_unset_or_malformed(monkeypatch, configured, expected):
    rates = {} if configured is None else {"checkout_order_mail_resend": configured}
    monkeypatch.setattr(OrderMailResendThrottle, "THROTTLE_RATES", rates)

    assert OrderMailResendThrottle().rate == expected


@pytest.mark.skipif(bool(os.environ.get("ENTIRIUS_TEST_NO_ACCESS")), reason="access path only")
def test_resend_needs_write_on_the_orders_area():
    """A read-only role on checkout.orders reads the detail but cannot resend."""
    pytest.importorskip("django_access")
    from django_access.services.route_map import classify, required_permission

    route = "api/checkout/v2/admin/<str:channel_idx>/orders/<str:uid>/mails/<str:kind>/resend/"
    info = classify(route, AdminOrderMailResendView.as_view())

    assert required_permission(info, "POST") == "checkout.orders:write"


@pytest.mark.django_db
def test_erasure_blanks_the_mail_recipients(channel):
    order = make_order(channel)
    row = _sent(order)
    OrderMail.objects.filter(pk=row.pk).update(last_error="SMTPRecipientsRefused: <recipient>")

    order.anonymize()

    assert list(OrderMail.objects.values_list("recipient", "last_error")) == [("", "")]
