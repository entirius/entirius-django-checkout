# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 customer order list — payment methods, localization, ownership, redaction.

``order_body["payment_method"]`` became a LIST with the multi-method (voucher)
refactor, but the list serializer still called ``.get("code")`` on it, so
``GET orders/list/`` raised ``AttributeError: 'list' object has no attribute 'get'``
and answered 500 for every order placed since 8.1.0. Nothing covered this endpoint,
which is why it shipped. These tests pin the list shape, the fallbacks and the fact
that resolving localized names stays a single query.

Views are called directly via ``APIRequestFactory`` (the module has no ROOT_URLCONF);
channel auth comes from the ``X-API-KEY`` header, customer auth from
``force_authenticate``.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.views.admin.order_views import AdminOrderDetailView
from django_checkout.api.v2.views.order_views import OrderAttachmentView, OrderDetailView, OrderListView
from django_checkout.models import Order, OrderStatusLabel, PaymentMethod
from django_checkout.utils import payment_entries

factory = APIRequestFactory()

VOUCHER_PM = {"code": "voucher", "name": "Voucher (stored)", "pay_code": "26WT****"}
TRANSFER_PM = {"code": "banktransfer", "name": "Bank Transfer (stored)"}


def _body(payment_method=None, **overrides):
    """Minimal order_body — top-level keys verified against real fenix-2 data."""
    body = {
        "total": "419.97",
        "total_tax": "78.53",
        "currency_code": "EUR",
        "country_code": "DE",
        "cart": {"items": [{"sku": "ENT-C002", "quantity": 1}]},
        "shipping_method": {"code": "europe-standard", "name": "Standard"},
        "payment_method": payment_method,
    }
    body.update(overrides)
    return body


def _order(channel, customer, body, status="UNPAID"):
    return Order.objects.create(channel=channel, customer=customer, order_status=status, order_body=body)


def _list(api_key, user, channel, **params):
    request = factory.get("/orders/list/", params, HTTP_X_API_KEY=api_key.key)
    force_authenticate(request, user=user)
    return OrderListView.as_view()(request, channel_idx=channel.idx)


def _first(response):
    assert response.status_code == 200, response.data
    return response.data["results"][0]


def _detail(api_key, user, channel, order):
    request = factory.get("/orders/x/", HTTP_X_API_KEY=api_key.key)
    force_authenticate(request, user=user)
    return OrderDetailView.as_view()(request, channel_idx=channel.idx, pretty_id=order.pretty_id)


@pytest.fixture
def transfer_method(channel):
    return PaymentMethod.objects.create(
        channel=channel, code="banktransfer", name_t9n={"en": "Bank Transfer", "de": "Banküberweisung"}
    )


@pytest.mark.django_db
class TestPaymentMethodsShape:
    def test_multi_method_order_lists_every_payment(self, api_key, regular_user, customer, channel, transfer_method):
        """The regression: a voucher settling alongside a gateway used to 500 the page."""
        _order(channel, customer, _body([VOUCHER_PM, TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel))

        assert [pm["code"] for pm in item["payment_methods"]] == ["voucher", "banktransfer"]

    def test_legacy_single_dict_is_normalized(self, api_key, regular_user, customer, channel, transfer_method):
        """Orders written before the multi-method refactor still hold a bare dict."""
        _order(channel, customer, _body(dict(TRANSFER_PM)))

        item = _first(_list(api_key, regular_user, channel))

        assert item["payment_methods"] == [{"code": "banktransfer", "name": "Bank Transfer"}]

    @pytest.mark.parametrize("payment_method", [None, [], {}, "banktransfer", [None, "x"]])
    def test_missing_or_junk_payment_method_yields_empty_list(
        self, api_key, regular_user, customer, channel, payment_method
    ):
        _order(channel, customer, _body(payment_method))

        item = _first(_list(api_key, regular_user, channel))

        assert item["payment_methods"] == []

    def test_payment_method_code_field_is_gone(self, api_key, regular_user, customer, channel):
        """Pins the contract change so the single-valued field cannot creep back."""
        _order(channel, customer, _body([TRANSFER_PM]))

        assert "payment_method_code" not in _first(_list(api_key, regular_user, channel))


@pytest.mark.django_db
class TestPaymentMethodNames:
    def test_name_is_localized_from_the_channel_row(self, api_key, regular_user, customer, channel, transfer_method):
        _order(channel, customer, _body([TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel, language="de"))

        assert item["payment_methods"][0]["name"] == "Banküberweisung"

    def test_without_language_param_the_channel_default_is_used(
        self, api_key, regular_user, customer, channel, transfer_method
    ):
        """Used to hardcode "en" regardless of the channel."""
        _order(channel, customer, _body([TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel))

        assert channel.default_language.iso2 == "en"
        assert item["payment_methods"][0]["name"] == "Bank Transfer"

    def test_name_falls_back_to_the_name_stored_on_the_order(self, api_key, regular_user, customer, channel):
        """No PaymentMethod row (deleted or renamed since) — the order still renders."""
        _order(channel, customer, _body([TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel))

        assert item["payment_methods"][0]["name"] == "Bank Transfer (stored)"

    def test_null_name_t9n_does_not_break(self, api_key, regular_user, customer, channel):
        PaymentMethod.objects.create(channel=channel, code="banktransfer", name_t9n=None)
        _order(channel, customer, _body([TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel))

        assert item["payment_methods"][0]["name"] == "Bank Transfer (stored)"


@pytest.mark.django_db
class TestListPayload:
    def test_totals_are_derived_from_the_order_body(self, api_key, regular_user, customer, channel):
        _order(channel, customer, _body([TRANSFER_PM]))

        item = _first(_list(api_key, regular_user, channel))

        assert item["total_gross"] == "419.97"
        assert item["total_tax"] == "78.53"
        assert item["total_net"] == "341.44"
        assert item["currency"] == "EUR"
        assert item["country_code"] == "DE"
        assert item["item_count"] == 1

    def test_status_label_falls_back_to_the_raw_status(self, api_key, regular_user, customer, channel):
        """No OrderStatusLabel for the channel — must not return null."""
        _order(channel, customer, _body([TRANSFER_PM]))

        assert _first(_list(api_key, regular_user, channel))["status_label"] == "UNPAID"

    def test_status_label_is_localized_when_present(self, api_key, regular_user, customer, channel):
        OrderStatusLabel.objects.create(channel=channel, status="UNPAID", name_t9n={"en": "Unpaid", "de": "Unbezahlt"})
        _order(channel, customer, _body([TRANSFER_PM]))

        assert _first(_list(api_key, regular_user, channel, language="de"))["status_label"] == "Unbezahlt"


@pytest.mark.django_db
class TestFilters:
    def test_status_filter_narrows_the_list(self, api_key, regular_user, customer, channel):
        _order(channel, customer, _body([TRANSFER_PM]), status="UNPAID")
        _order(channel, customer, _body([TRANSFER_PM]), status="CONFIRMED")

        response = _list(api_key, regular_user, channel, status="CONFIRMED")

        assert response.data["count"] == 1
        assert response.data["results"][0]["status"] == "CONFIRMED"

    def test_order_id_matches_exactly_and_not_by_substring(self, api_key, regular_user, customer, channel):
        """Exact match only — a substring match would allow order-ID enumeration."""
        order = _order(channel, customer, _body([TRANSFER_PM]))
        pretty_id = order.pretty_id

        assert _list(api_key, regular_user, channel, order_id=pretty_id).data["count"] == 1
        assert _list(api_key, regular_user, channel, order_id=pretty_id[:-1]).data["count"] == 0

    def test_ordering_reverses_and_ignores_unknown_values(self, api_key, regular_user, customer, channel):
        older = _order(channel, customer, _body([TRANSFER_PM]))
        newer = _order(channel, customer, _body([TRANSFER_PM]))
        Order.objects.filter(pk=older.pk).update(created="2020-01-01T00:00:00Z")

        default = _list(api_key, regular_user, channel)
        ascending = _list(api_key, regular_user, channel, ordering="created")
        bogus = _list(api_key, regular_user, channel, ordering="; DROP TABLE")

        assert default.data["results"][0]["order_id"] == str(newer.order_id)
        assert ascending.data["results"][0]["order_id"] == str(older.order_id)
        assert bogus.status_code == 200
        assert bogus.data["results"][0]["order_id"] == str(newer.order_id)


@pytest.mark.django_db
class TestMalformedOrderBody:
    """order_body is a free-form legacy blob — junk must not 500 the whole page."""

    @pytest.mark.parametrize("total", ["not-a-number", "", {}, []])
    def test_unparseable_total_does_not_break_the_page(self, api_key, regular_user, customer, channel, total):
        _order(channel, customer, _body([TRANSFER_PM], total=total))

        item = _first(_list(api_key, regular_user, channel))

        assert item["total_gross"] is None
        assert item["total_net"] is None

    def test_unparseable_tax_still_yields_a_gross_total(self, api_key, regular_user, customer, channel):
        _order(channel, customer, _body([TRANSFER_PM], total_tax="junk"))

        item = _first(_list(api_key, regular_user, channel))

        assert item["total_gross"] == "419.97"
        assert item["total_net"] == "419.97"


@pytest.mark.django_db
class TestAccessAndPaging:
    def test_user_without_customer_row_is_unauthorized(self, api_key, channel, django_user_model):
        """get_customer() resolves through user.customer — no row means no identity."""
        user_without_customer = django_user_model.objects.create_user(username="no-customer")

        response = _list(api_key, user_without_customer, channel)

        assert response.status_code == 401

    def test_another_customers_orders_are_not_listed(self, api_key, regular_user, customer, channel, django_user_model):
        from django_accounts.models import Customer

        other = Customer.objects.create(user=django_user_model.objects.create_user(username="other"))
        _order(channel, other, _body([TRANSFER_PM]))

        response = _list(api_key, regular_user, channel)

        assert response.data["count"] == 0

    def test_pagination_reports_count_and_links(self, api_key, regular_user, customer, channel):
        for _ in range(3):
            _order(channel, customer, _body([TRANSFER_PM]))

        first = _list(api_key, regular_user, channel, page=1, page_size=2)
        second = _list(api_key, regular_user, channel, page=2, page_size=2)

        assert first.data["count"] == 3
        assert len(first.data["results"]) == 2
        assert first.data["next"] and first.data["previous"] is None
        assert len(second.data["results"]) == 1
        assert second.data["next"] is None and second.data["previous"]

    def test_query_count_does_not_grow_with_the_page(self, api_key, regular_user, customer, channel, transfer_method):
        """Guards the bulk name lookup — resolving names per order would be an N+1."""
        _order(channel, customer, _body([VOUCHER_PM, TRANSFER_PM]))
        with CaptureQueriesContext(connection) as one_order:
            _list(api_key, regular_user, channel)

        for _ in range(4):
            _order(channel, customer, _body([VOUCHER_PM, TRANSFER_PM]))
        with CaptureQueriesContext(connection) as five_orders:
            _list(api_key, regular_user, channel)

        assert len(five_orders) == len(one_order)


@pytest.mark.django_db
class TestGuestOrderIsolation:
    """A user with no Customer row must not inherit ownership of every guest order.

    `get_customer()` returns None for such users, and `customer=None` matches guest
    orders — combined with sequential pretty_ids that allowed walking the whole channel.
    """

    def test_detail_of_a_guest_order_is_refused(self, api_key, channel, django_user_model):
        guest_order = _order(channel, None, _body([TRANSFER_PM]))
        user = django_user_model.objects.create_user(username="no-customer-detail")

        response = _detail(api_key, user, channel, guest_order)

        assert response.status_code == 401

    def test_attachment_of_a_guest_order_is_refused(self, api_key, channel, django_user_model):
        guest_order = _order(channel, None, _body([TRANSFER_PM]))
        user = django_user_model.objects.create_user(username="no-customer-file")

        request = factory.get("/orders/x/files/1/", HTTP_X_API_KEY=api_key.key)
        force_authenticate(request, user=user)
        response = OrderAttachmentView.as_view()(
            request, channel_idx=channel.idx, order_id=guest_order.pretty_id, file_id=1
        )

        assert response.status_code == 401

    def test_customer_still_reads_their_own_order(self, api_key, regular_user, customer, channel):
        own = _order(channel, customer, _body([TRANSFER_PM]))

        assert _detail(api_key, regular_user, channel, own).status_code == 200


class TestPaymentEntriesContract:
    """redact_payment_secrets masks in place, so the entries must stay live references."""

    def test_list_entries_are_live_references(self):
        body = {"payment_method": [{"code": "payu"}]}

        assert payment_entries(body)[0] is body["payment_method"][0]

    def test_legacy_dict_entry_is_a_live_reference(self):
        body = {"payment_method": {"code": "payu"}}

        assert payment_entries(body)[0] is body["payment_method"]


@pytest.mark.django_db
class TestPaymentSecretRedaction:
    SECRETS = {"card": "TOK_123", "authorization_token": "auth-abc", "continue_url": "https://psp/return?sig=x"}

    def _order_with_secrets(self, channel, customer):
        return _order(channel, customer, _body([{"code": "payu", **self.SECRETS}]))

    def test_customer_detail_redacts_secrets(self, api_key, regular_user, customer, channel):
        """The customer detail view returned order_body completely raw."""
        order = self._order_with_secrets(channel, customer)

        request = factory.get("/orders/x/", HTTP_X_API_KEY=api_key.key)
        force_authenticate(request, user=regular_user)
        response = OrderDetailView.as_view()(request, channel_idx=channel.idx, pretty_id=order.pretty_id)

        assert response.status_code == 200, response.data
        entry = response.data["order_body"]["payment_method"][0]
        assert all(entry[key] == "***REDACTED***" for key in self.SECRETS)

    def test_admin_detail_redacts_list_shaped_secrets(self, api_key, admin_user, customer, channel):
        """Redaction only handled a dict, so multi-method orders leaked every secret."""
        order = self._order_with_secrets(channel, customer)

        request = factory.get("/admin/orders/x/", HTTP_X_API_KEY=api_key.key)
        force_authenticate(request, user=admin_user)
        response = AdminOrderDetailView.as_view()(request, channel_idx=channel.idx, uid=order.pretty_id)

        assert response.status_code == 200, response.data
        entry = response.data["order_body"]["payment_method"][0]
        assert all(entry[key] == "***REDACTED***" for key in self.SECRETS)

    def test_unset_secret_stays_null(self, api_key, regular_user, customer, channel):
        """Masking a null would claim a secret exists where none was ever stored."""
        order = _order(channel, customer, _body([{"code": "banktransfer", "card": None}]))

        request = factory.get("/orders/x/", HTTP_X_API_KEY=api_key.key)
        force_authenticate(request, user=regular_user)
        response = OrderDetailView.as_view()(request, channel_idx=channel.idx, pretty_id=order.pretty_id)

        assert response.data["order_body"]["payment_method"][0]["card"] is None

    def test_blik_pay_code_is_redacted(self, api_key, regular_user, customer, channel):
        """pay_code is a payment credential for BLIK and was stored unmasked."""
        order = _order(channel, customer, _body([{"code": "payu_blik", "pay_code": "123456"}]))

        response = _detail(api_key, regular_user, channel, order)

        assert response.data["order_body"]["payment_method"][0]["pay_code"] == "***REDACTED***"

    def test_already_masked_voucher_pay_code_stays_readable(self, api_key, regular_user, customer, channel):
        """Voucher codes are persisted masked and CMS support reads them."""
        order = _order(channel, customer, _body([{"code": "voucher", "pay_code": "26WT********"}]))

        response = _detail(api_key, regular_user, channel, order)

        assert response.data["order_body"]["payment_method"][0]["pay_code"] == "26WT********"

    def test_redaction_does_not_touch_the_stored_row(self, api_key, regular_user, customer, channel):
        order = self._order_with_secrets(channel, customer)

        request = factory.get("/orders/x/", HTTP_X_API_KEY=api_key.key)
        force_authenticate(request, user=regular_user)
        OrderDetailView.as_view()(request, channel_idx=channel.idx, pretty_id=order.pretty_id)

        order.refresh_from_db()
        assert order.order_body["payment_method"][0]["card"] == "TOK_123"
