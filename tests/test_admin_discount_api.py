# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Admin Discount API tests.

Views are called directly via APIRequestFactory (the module has no ROOT_URLCONF, and
django_checkout.urls pulls in payment-provider deps). Covers the auth matrix, rule/code/
filter CRUD, channel scoping, and pagination.
"""

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.views.admin.discount_views import (
    AdminCurrencyListView,
    AdminCustomerFilterDetailView,
    AdminCustomerFilterListView,
    AdminDiscountCodeDetailView,
    AdminDiscountCodeListView,
    AdminDiscountMetaView,
    AdminDiscountRuleBulkView,
    AdminDiscountRuleDetailView,
    AdminDiscountRuleListView,
    AdminProductFilterDetailView,
    AdminProductFilterListView,
    AdminShippingMethodListView,
)
from django_checkout.schemas.requests.discount import DiscountRuleCreateInput
from django_checkout.services import discount_service

factory = APIRequestFactory()


@pytest.fixture
def other_channel(db, default_language, default_currency, default_country):
    from django_checkout.models import Channel

    return Channel.objects.create(
        idx="other-channel",
        label="Other",
        min_order_price=0,
        default_language=default_language,
        default_currency=default_currency,
        default_country=default_country,
    )


def _make_rule(channel, **overrides):
    payload = {"modifier": "percent_discount", "extra_value": 10, "name": "Test rule"}
    payload.update(overrides)
    return discount_service.create_rule(channel, DiscountRuleCreateInput(**payload))


def _call(view, method, path, user=None, *, data=None, **kwargs):
    request = (
        getattr(factory, method)(path, data, format="json") if data is not None else getattr(factory, method)(path)
    )
    if user is not None:
        force_authenticate(request, user=user)
    return view.as_view()(request, **kwargs)


# --- Metadata ---


def test_discount_meta(channel, admin_user):
    response = _call(AdminDiscountMetaView, "get", "/discount-meta/", admin_user, channel_idx=channel.idx)
    assert response.status_code == 200
    assert len(response.data["modifiers"]) >= 12
    assert {t["value"] for t in response.data["targets"]} == {"all", "first_order_logged", "first_order_all"}
    percent = next(m for m in response.data["modifiers"] if m["value"] == "percent_discount")
    assert percent["extra_value_kind"] == "percent"
    gratis_sku = next(m for m in response.data["modifiers"] if m["value"] == "gratis_by_sku_in_cart")
    assert gratis_sku["extra_value_kind"] == "gratis_sku"


def test_list_shipping_methods(channel, admin_user):
    from django_checkout.models import ShippingMethod

    ShippingMethod.objects.create(channel=channel, code="dhl", name_t9n={"en": "DHL Courier"})
    ShippingMethod.objects.create(channel=channel, code="dpd")
    response = _call(AdminShippingMethodListView, "get", "/shipping-methods/", admin_user, channel_idx=channel.idx)
    assert response.status_code == 200
    assert {m["code"] for m in response.data["results"]} == {"dhl", "dpd"}
    dhl = next(m for m in response.data["results"] if m["code"] == "dhl")
    assert dhl["channel_idx"] == channel.idx
    assert dhl["name"] == "DHL Courier"


def test_list_currencies(channel, admin_user):
    response = _call(AdminCurrencyListView, "get", "/currencies/", admin_user, channel_idx=channel.idx)
    assert response.status_code == 200
    assert any(c["iso3"] == "EUR" for c in response.data["results"])


# --- Auth matrix ---


def test_list_requires_authentication(channel):
    response = _call(AdminDiscountRuleListView, "get", "/discount-rules/", channel_idx=channel.idx)
    assert response.status_code == 401


def test_list_forbidden_for_regular_user(channel, regular_user):
    response = _call(AdminDiscountRuleListView, "get", "/discount-rules/", regular_user, channel_idx=channel.idx)
    assert response.status_code == 403


def test_list_ok_for_admin(channel, admin_user):
    response = _call(AdminDiscountRuleListView, "get", "/discount-rules/", admin_user, channel_idx=channel.idx)
    assert response.status_code == 200
    assert response.data["count"] == 0


# --- Rule CRUD ---


def test_create_rule(channel, admin_user):
    body = {"modifier": "percent_discount", "extra_value": 10, "name": "Summer -20%", "currencies": ["EUR"]}
    response = _call(
        AdminDiscountRuleListView, "post", "/discount-rules/", admin_user, data=body, channel_idx=channel.idx
    )
    assert response.status_code == 201
    assert response.data["modifier"] == "percent_discount"
    assert response.data["extra_value"] == 10
    assert channel.idx in response.data["channels"]  # URL channel auto-added


def test_create_rule_invalid_modifier_returns_400(channel, admin_user):
    body = {"modifier": "not_a_real_modifier", "extra_value": 10}
    response = _call(
        AdminDiscountRuleListView, "post", "/discount-rules/", admin_user, data=body, channel_idx=channel.idx
    )
    assert response.status_code == 400


def test_create_rule_rejects_non_object_body(channel, admin_user):
    response = _call(
        AdminDiscountRuleListView, "post", "/discount-rules/", admin_user, data=[1, 2], channel_idx=channel.idx
    )
    assert response.status_code == 400


def test_retrieve_rule(channel, admin_user):
    rule = _make_rule(channel)
    response = _call(AdminDiscountRuleDetailView, "get", "/", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert response.status_code == 200
    assert response.data["id"] == rule.pk
    assert response.data["code_count"] == 0
    assert response.data["product_filters"] == []


def test_retrieve_rule_from_other_channel_returns_404(channel, other_channel, admin_user):
    rule = _make_rule(other_channel)
    response = _call(AdminDiscountRuleDetailView, "get", "/", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert response.status_code == 404


def test_update_rule(channel, admin_user):
    rule = _make_rule(channel, is_active=True)
    body = {"name": "Renamed", "is_active": False}
    response = _call(
        AdminDiscountRuleDetailView, "patch", "/", admin_user, data=body, channel_idx=channel.idx, rule_id=rule.pk
    )
    assert response.status_code == 200
    assert response.data["name"] == "Renamed"
    assert response.data["is_active"] is False


def test_delete_rule(channel, admin_user):
    rule = _make_rule(channel)
    response = _call(AdminDiscountRuleDetailView, "delete", "/", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert response.status_code == 204
    from django_checkout.models import DiscountRuleCode

    assert not DiscountRuleCode.objects.filter(pk=rule.pk).exists()


# --- Bulk actions ---


def test_bulk_deactivate_by_ids(channel, admin_user):
    from django_checkout.models import DiscountRuleCode

    r1 = _make_rule(channel, is_active=True)
    r2 = _make_rule(channel, is_active=True)
    resp = _call(
        AdminDiscountRuleBulkView,
        "post",
        "/discount-rules/bulk/",
        admin_user,
        data={"action": "deactivate", "ids": [r1.pk, r2.pk]},
        channel_idx=channel.idx,
    )
    assert resp.status_code == 200
    assert resp.data["affected"] == 2
    assert not DiscountRuleCode.objects.get(pk=r1.pk).is_active
    assert not DiscountRuleCode.objects.get(pk=r2.pk).is_active


def test_bulk_delete_by_ids(channel, admin_user):
    from django_checkout.models import DiscountRuleCode

    rule = _make_rule(channel)
    resp = _call(
        AdminDiscountRuleBulkView,
        "post",
        "/discount-rules/bulk/",
        admin_user,
        data={"action": "delete", "ids": [rule.pk]},
        channel_idx=channel.idx,
    )
    assert resp.status_code == 200
    assert resp.data["affected"] == 1
    assert not DiscountRuleCode.objects.filter(pk=rule.pk).exists()


def test_bulk_all_matching_respects_filters(channel, admin_user):
    from django_checkout.models import DiscountRuleCode

    _make_rule(channel, is_active=True, name="keep-active")
    inactive1 = _make_rule(channel, is_active=False)
    inactive2 = _make_rule(channel, is_active=False)
    resp = _call(
        AdminDiscountRuleBulkView,
        "post",
        "/discount-rules/bulk/",
        admin_user,
        data={"action": "activate", "all_matching": True, "filters": {"is_active": "false"}},
        channel_idx=channel.idx,
    )
    assert resp.status_code == 200
    assert resp.data["affected"] == 2
    assert DiscountRuleCode.objects.get(pk=inactive1.pk).is_active
    assert DiscountRuleCode.objects.get(pk=inactive2.pk).is_active


def test_bulk_invalid_action_returns_400(channel, admin_user):
    rule = _make_rule(channel)
    resp = _call(
        AdminDiscountRuleBulkView,
        "post",
        "/discount-rules/bulk/",
        admin_user,
        data={"action": "nuke", "ids": [rule.pk]},
        channel_idx=channel.idx,
    )
    assert resp.status_code == 400


# --- Codes ---


def test_create_and_list_codes(channel, admin_user):
    rule = _make_rule(channel)
    body = {"code": "SUMMER20", "max_used": 5}
    created = _call(
        AdminDiscountCodeListView, "post", "/codes/", admin_user, data=body, channel_idx=channel.idx, rule_id=rule.pk
    )
    assert created.status_code == 201
    assert created.data["code"] == "SUMMER20"
    assert created.data["current_used"] == 0

    listing = _call(AdminDiscountCodeListView, "get", "/codes/", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert listing.status_code == 200
    assert listing.data["count"] == 1
    assert len(listing.data["results"]) == 1


def test_list_codes_search_and_ordering(channel, admin_user):
    from django_checkout.schemas.requests.discount import DiscountCodeInput

    rule = _make_rule(channel)
    for code in ["ALPHA", "BETA", "GAMMA"]:
        discount_service.create_code(channel, rule.pk, DiscountCodeInput(code=code))

    found = _call(
        AdminDiscountCodeListView, "get", "/codes/?search=ET", admin_user, channel_idx=channel.idx, rule_id=rule.pk
    )
    assert found.data["count"] == 1
    assert found.data["results"][0]["code"] == "BETA"

    page = _call(
        AdminDiscountCodeListView,
        "get",
        "/codes/?ordering=code&page_size=2",
        admin_user,
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    assert page.data["count"] == 3
    assert [c["code"] for c in page.data["results"]] == ["ALPHA", "BETA"]
    assert page.data["next"] is not None


def test_update_and_delete_code(channel, admin_user):
    from django_checkout.schemas.requests.discount import DiscountCodeInput

    rule = _make_rule(channel)
    code = discount_service.create_code(channel, rule.pk, DiscountCodeInput(code="X1", max_used=1))

    updated = _call(
        AdminDiscountCodeDetailView,
        "patch",
        "/",
        admin_user,
        data={"max_used": 10},
        channel_idx=channel.idx,
        rule_id=rule.pk,
        code_id=code.pk,
    )
    assert updated.status_code == 200
    assert updated.data["max_used"] == 10

    deleted = _call(
        AdminDiscountCodeDetailView,
        "delete",
        "/",
        admin_user,
        channel_idx=channel.idx,
        rule_id=rule.pk,
        code_id=code.pk,
    )
    assert deleted.status_code == 204


def test_create_code_invalid_date_window_returns_400(channel, admin_user):
    rule = _make_rule(channel)
    body = {"code": "BAD", "active_from": "2026-06-30", "active_to": "2026-06-01"}
    response = _call(
        AdminDiscountCodeListView, "post", "/codes/", admin_user, data=body, channel_idx=channel.idx, rule_id=rule.pk
    )
    assert response.status_code == 400


# --- Product filter (no PIM data needed: scalar criteria only) ---


def test_create_product_filter(channel, admin_user):
    rule = _make_rule(channel)
    body = {"is_inclusion_or_exclusion": "inclusion", "qty_from": 2, "qty_to": 10}
    response = _call(
        AdminProductFilterListView,
        "post",
        "/product-filters/",
        admin_user,
        data=body,
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    assert response.status_code == 201
    assert response.data["qty_from"] == 2

    detail = _call(AdminDiscountRuleDetailView, "get", "/", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert len(detail.data["product_filters"]) == 1


def test_create_product_filter_invalid_inclusion_returns_400(channel, admin_user):
    rule = _make_rule(channel)
    body = {"is_inclusion_or_exclusion": "nonsense"}
    response = _call(
        AdminProductFilterListView,
        "post",
        "/product-filters/",
        admin_user,
        data=body,
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    assert response.status_code == 400


def test_delete_product_filter(channel, admin_user):
    rule = _make_rule(channel)
    created = _call(
        AdminProductFilterListView,
        "post",
        "/product-filters/",
        admin_user,
        data={"is_inclusion_or_exclusion": "inclusion"},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    filter_id = created.data["id"]
    deleted = _call(
        AdminProductFilterDetailView,
        "delete",
        "/",
        admin_user,
        channel_idx=channel.idx,
        rule_id=rule.pk,
        filter_id=filter_id,
    )
    assert deleted.status_code == 204
    listing = _call(
        AdminProductFilterListView, "get", "/product-filters/", admin_user, channel_idx=channel.idx, rule_id=rule.pk
    )
    assert listing.data["results"] == []


def test_update_product_filter_full_replace(channel, admin_user):
    rule = _make_rule(channel)
    created = _call(
        AdminProductFilterListView,
        "post",
        "/product-filters/",
        admin_user,
        data={"is_inclusion_or_exclusion": "inclusion", "qty_from": 2},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    filter_id = created.data["id"]
    body = {
        "is_inclusion_or_exclusion": "exclusion",
        "take_common_part": True,
        "qty_from": 3,
        "qty_to": 20,
        "products": [],
        "categories": [],
    }
    response = _call(
        AdminProductFilterDetailView,
        "patch",
        "/",
        admin_user,
        data=body,
        channel_idx=channel.idx,
        rule_id=rule.pk,
        filter_id=filter_id,
    )
    assert response.status_code == 200
    assert response.data["is_inclusion_or_exclusion"] == "exclusion"
    assert response.data["take_common_part"] is True
    assert response.data["qty_from"] == 3
    assert response.data["qty_to"] == 20


# --- Customer filter (Group is simple to create) ---


def test_create_customer_filter_with_group(channel, admin_user, db):
    from django_accounts.models import Group

    Group.objects.create(code="vip", name="VIP")
    rule = _make_rule(channel)
    body = {"is_inclusion_or_exclusion": "inclusion", "groups": ["vip"]}
    response = _call(
        AdminCustomerFilterListView,
        "post",
        "/customer-filters/",
        admin_user,
        data=body,
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    assert response.status_code == 201
    assert response.data["groups"] == ["vip"]


def test_update_customer_filter_full_replace(channel, admin_user, db):
    from django_accounts.models import Group

    Group.objects.create(code="vip", name="VIP")
    Group.objects.create(code="gold", name="Gold")
    rule = _make_rule(channel)
    created = _call(
        AdminCustomerFilterListView,
        "post",
        "/customer-filters/",
        admin_user,
        data={"is_inclusion_or_exclusion": "inclusion", "groups": ["vip"]},
        channel_idx=channel.idx,
        rule_id=rule.pk,
    )
    filter_id = created.data["id"]
    body = {"is_inclusion_or_exclusion": "exclusion", "take_common_part": False, "customers": [], "groups": ["gold"]}
    response = _call(
        AdminCustomerFilterDetailView,
        "patch",
        "/",
        admin_user,
        data=body,
        channel_idx=channel.idx,
        rule_id=rule.pk,
        filter_id=filter_id,
    )
    assert response.status_code == 200
    assert response.data["is_inclusion_or_exclusion"] == "exclusion"
    assert response.data["groups"] == ["gold"]  # full replace: vip dropped, gold set


# --- Pagination ---


def test_list_pagination(channel, admin_user):
    for i in range(25):
        _make_rule(channel, name=f"Rule {i}")
    response = _call(
        AdminDiscountRuleListView, "get", "/discount-rules/?page_size=10", admin_user, channel_idx=channel.idx
    )
    assert response.status_code == 200
    assert response.data["count"] == 25
    assert len(response.data["results"]) == 10
    assert response.data["next"] is not None
