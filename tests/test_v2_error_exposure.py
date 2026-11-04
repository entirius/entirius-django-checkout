# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""No exception text in API responses (CodeQL ``py/stack-trace-exposure``).

A refusal checkout wrote for the client (``ClientError``) keeps its status and message. Any other exception from
below the admin discount views goes to the v2 handler: 500 ``INTERNAL_ERROR`` with a ``debug_id`` and nothing of the
exception's text. The shipping step of the cart answers a fixed message when delivery prices fail.
"""

import logging
from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.test import APIRequestFactory, force_authenticate

from django_checkout.api.v2.views.admin import discount_views as views
from django_checkout.domain.validators.shipping import validate_shipping_method
from django_checkout.schemas.requests.discount import DiscountRuleCreateInput
from django_checkout.services import discount_service

factory = APIRequestFactory()
TRAP = "OperationalError: /srv/app/.env token=sk-leak-4f2a9c host=db.internal:5432"

RULE_BODY = {"modifier": "percent_discount", "extra_value": 10}
FILTER_BODY = {"is_inclusion_or_exclusion": "inclusion"}

# (view, method, service function, body, URL kwargs) of every route that catches a service refusal.
RULE = {"rule_id": 1}
CODE = {"rule_id": 1, "code_id": 1}
FILTER = {"rule_id": 1, "filter_id": 1}
BAD_REQUEST_ROUTES = [
    (views.AdminDiscountRuleListView, "post", "create_rule", RULE_BODY, {}),
    (views.AdminDiscountRuleBulkView, "post", "bulk_rules", {"action": "activate", "ids": [1]}, {}),
    (views.AdminDiscountRuleDetailView, "patch", "update_rule", {"name": "x"}, RULE),
    (views.AdminProductFilterListView, "post", "create_product_filter", FILTER_BODY, RULE),
    (views.AdminProductFilterDetailView, "patch", "update_product_filter", FILTER_BODY, FILTER),
    (views.AdminThresholdFilterListView, "post", "create_product_filter", FILTER_BODY, RULE),
    (views.AdminThresholdFilterDetailView, "patch", "update_product_filter", FILTER_BODY, FILTER),
    (views.AdminCustomerFilterListView, "post", "create_customer_filter", FILTER_BODY, RULE),
    (views.AdminCustomerFilterDetailView, "patch", "update_customer_filter", FILTER_BODY, FILTER),
]
NOT_FOUND_ROUTES = [
    (views.AdminDiscountRuleDetailView, "get", "get_rule", None, RULE),
    (views.AdminDiscountRuleDetailView, "patch", "update_rule", {"name": "x"}, RULE),
    (views.AdminDiscountRuleDetailView, "delete", "delete_rule", None, RULE),
    (views.AdminDiscountCodeListView, "get", "list_codes", None, RULE),
    (views.AdminDiscountCodeListView, "post", "create_code", {"code": "SUMMER"}, RULE),
    (views.AdminDiscountCodeDetailView, "patch", "update_code", {"max_used": 2}, CODE),
    (views.AdminDiscountCodeDetailView, "delete", "delete_code", None, CODE),
    (views.AdminProductFilterListView, "get", "list_product_filters", None, RULE),
    (views.AdminProductFilterListView, "post", "create_product_filter", FILTER_BODY, RULE),
    (views.AdminProductFilterDetailView, "patch", "update_product_filter", FILTER_BODY, FILTER),
    (views.AdminProductFilterDetailView, "delete", "delete_product_filter", None, FILTER),
    (views.AdminThresholdFilterListView, "get", "list_product_filters", None, RULE),
    (views.AdminThresholdFilterListView, "post", "create_product_filter", FILTER_BODY, RULE),
    (views.AdminThresholdFilterDetailView, "patch", "update_product_filter", FILTER_BODY, FILTER),
    (views.AdminThresholdFilterDetailView, "delete", "delete_product_filter", None, FILTER),
    (views.AdminCustomerFilterListView, "get", "list_customer_filters", None, RULE),
    (views.AdminCustomerFilterListView, "post", "create_customer_filter", FILTER_BODY, RULE),
    (views.AdminCustomerFilterDetailView, "patch", "update_customer_filter", FILTER_BODY, FILTER),
    (views.AdminCustomerFilterDetailView, "delete", "delete_customer_filter", None, FILTER),
]


def _route_id(route) -> str:
    view, method, service_fn, *_ = route
    return f"{view.__name__}.{method}-{service_fn}"


@pytest.fixture(autouse=True)
def v2_errors(settings):
    """The v2 error envelope, as the service wires it (the module's test settings keep DRF's default handler)."""
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "EXCEPTION_HANDLER": "django_utils.api.v2_errors.v2_exception_handler",
    }


def _call(view, method, user, data=None, **kwargs):
    request = getattr(factory, method)("/", data, format="json")
    force_authenticate(request, user=user)
    return view.as_view()(request, **kwargs)


def _call_with_failing_service(route, channel, user, error: Exception):
    view, method, service_fn, body, url_kwargs = route
    with mock.patch.object(discount_service, service_fn, side_effect=error):
        return _call(view, method, user, body, channel_idx=channel.idx, **url_kwargs)


def _assert_internal_error(response) -> None:
    assert response.status_code == 500, response.data
    assert response.data["error"] == "INTERNAL_ERROR"
    assert response.data["debug_id"]
    assert response.data["details"] == []
    body = str(response.data)
    assert "sk-leak" not in body
    assert "/srv/app" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("route", BAD_REQUEST_ROUTES, ids=_route_id)
def test_admin_discount_hides_a_lower_layer_value_error(route, channel, admin_user):
    _assert_internal_error(_call_with_failing_service(route, channel, admin_user, ValueError(TRAP)))


@pytest.mark.django_db
@pytest.mark.parametrize("route", NOT_FOUND_ROUTES, ids=_route_id)
def test_admin_discount_hides_a_lower_layer_does_not_exist(route, channel, admin_user):
    _assert_internal_error(_call_with_failing_service(route, channel, admin_user, ObjectDoesNotExist(TRAP)))


@pytest.mark.django_db
def test_admin_discount_invalid_modifier_keeps_its_message(channel, admin_user):
    body = {"modifier": "not_a_real_modifier", "extra_value": 10}
    response = _call(views.AdminDiscountRuleListView, "post", admin_user, body, channel_idx=channel.idx)
    assert response.status_code == 400
    assert response.data["error"] == "VALIDATION_ERROR"
    assert response.data["message"].startswith("Invalid modifier 'not_a_real_modifier'. Allowed: [")


@pytest.mark.django_db
def test_admin_discount_invalid_bulk_action_keeps_its_message(channel, admin_user):
    body = {"action": "explode", "ids": [1]}
    response = _call(views.AdminDiscountRuleBulkView, "post", admin_user, body, channel_idx=channel.idx)
    assert response.status_code == 400
    assert response.data == {
        "error": "VALIDATION_ERROR",
        "message": "Invalid action 'explode'. Allowed: ['activate', 'deactivate', 'delete']",
    }


@pytest.mark.django_db
def test_admin_discount_rule_of_another_channel_keeps_its_message(channel, other_channel, admin_user):
    rule = discount_service.create_rule(other_channel, DiscountRuleCreateInput(**RULE_BODY))
    response = _call(views.AdminDiscountRuleDetailView, "get", admin_user, channel_idx=channel.idx, rule_id=rule.pk)
    assert response.status_code == 404
    assert response.data == {
        "error": "NOT_FOUND",
        "message": f"Discount rule {rule.pk} not found in channel {channel.idx}",
    }


@pytest.mark.django_db
def test_admin_discount_missing_code_keeps_its_message(channel, admin_user):
    rule = discount_service.create_rule(channel, DiscountRuleCreateInput(**RULE_BODY))
    response = _call(
        views.AdminDiscountCodeDetailView,
        "delete",
        admin_user,
        channel_idx=channel.idx,
        rule_id=rule.pk,
        code_id=999999,
    )
    assert response.status_code == 404
    assert response.data == {"error": "NOT_FOUND", "message": f"Discount code 999999 not found in rule {rule.pk}"}


def test_shipping_price_failure_answers_a_fixed_cart_message(caplog):
    shipping = SimpleNamespace(code="dpd", country_code="PL", delivery_point=None)
    option = mock.Mock(pk=7, cash_on_delivery_fee=None)
    with (
        mock.patch(
            "django_checkout.domain.validators.shipping.get_delivery_matrix_prices", side_effect=ValueError(TRAP)
        ),
        caplog.at_level(logging.WARNING, logger="checkout.shipping"),
    ):
        validated, message = validate_shipping_method(mock.Mock(), shipping, shipping, option, object(), "PL", "en", [])
    assert message == "Error while getting delivery prices. "
    assert validated.code == "dpd"
    assert TRAP in caplog.text  # the cause stays in the log
