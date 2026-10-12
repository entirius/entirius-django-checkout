# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Admin Discount API — discount rules, codes, and filters for CMS.

JWT + IsAdminUser. Channel-scoped via CheckoutChannelMixin. All ORM/business logic lives in
services.discount_service; views parse the request, delegate, and shape the response.

Resource map (under api-admin/checkout/v2/{channel_idx}/):
    discount-rules/                                  GET list, POST create
    discount-rules/{rule_id}/                        GET, PATCH, DELETE
    discount-rules/{rule_id}/codes/                  GET list, POST create
    discount-rules/{rule_id}/codes/{code_id}/        PATCH, DELETE
    discount-rules/{rule_id}/product-filters/        GET list, POST create
    discount-rules/{rule_id}/product-filters/{id}/   PATCH, DELETE
    discount-rules/{rule_id}/threshold-filters/      GET list, POST create
    discount-rules/{rule_id}/threshold-filters/{id}/ PATCH, DELETE
    discount-rules/{rule_id}/customer-filters/       GET list, POST create
    discount-rules/{rule_id}/customer-filters/{id}/  PATCH, DELETE
"""

from django.core.exceptions import ObjectDoesNotExist
from drf_spectacular.utils import OpenApiParameter, extend_schema
from pydantic import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_checkout.api.v2.exceptions import raise_pydantic_as_drf
from django_checkout.api.v2.mixins import CheckoutChannelMixin
from django_checkout.api.v2.pagination import AdminPageNumberPagination
from django_checkout.schemas.requests.discount import (
    CustomerFilterInput,
    DiscountCodeInput,
    DiscountCodeUpdateInput,
    DiscountRuleBulkRequest,
    DiscountRuleCreateInput,
    DiscountRuleUpdateInput,
    ProductFilterInput,
)
from django_checkout.schemas.responses.discount import (
    ChannelListResponse,
    CurrencyListResponse,
    CustomerFilterListResponse,
    CustomerFilterResponse,
    DiscountCodeListResponse,
    DiscountCodeResponse,
    DiscountMetaResponse,
    DiscountRuleBulkResponse,
    DiscountRuleDetailResponse,
    DiscountRuleListResponse,
    ProductFilterListResponse,
    ProductFilterResponse,
    ShippingMethodListResponse,
)
from django_checkout.services import discount_service

_TAG = "Checkout Admin"


class _AdminView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.discounts"


def _bad_request(message: str) -> Response:
    return Response({"error": "VALIDATION_ERROR", "message": message}, status=400)


def _not_found(message: str = "Not found.") -> Response:
    return Response({"error": "NOT_FOUND", "message": message}, status=404)


def _read(schema_cls, request):
    """Parse a JSON object body into a Pydantic schema or raise a DRF 400."""
    if not isinstance(request.data, dict):
        raise DRFValidationError({"__all__": ["Expected a JSON object."]})
    try:
        return schema_cls(**request.data)
    except ValidationError as exc:
        raise_pydantic_as_drf(exc)


# --- Metadata ---


class AdminDiscountMetaView(_AdminView):
    @extend_schema(
        summary="Discount form metadata",
        description="Modifier/target/inclusion choices with per-modifier extra_value hints for data-driven CMS forms.",
        tags=[_TAG],
        responses={200: DiscountMetaResponse},
    )
    def get(self, request, channel_idx, **kwargs):
        return Response(discount_service.get_discount_meta())


class AdminShippingMethodListView(_AdminView):
    @extend_schema(
        summary="List shipping methods",
        description="Shipping methods for the channel — options for a rule's free_shipping_methods multi-select.",
        tags=[_TAG],
        responses={200: ShippingMethodListResponse},
    )
    def get(self, request, channel_idx, **kwargs):
        return Response({"results": discount_service.list_shipping_methods(self.get_channel())})


class AdminCurrencyListView(_AdminView):
    @extend_schema(
        summary="List currencies",
        description="Currencies for the channel — options for a rule's currencies multi-select and per-currency thresholds.",
        tags=[_TAG],
        responses={200: CurrencyListResponse},
    )
    def get(self, request, channel_idx, **kwargs):
        return Response({"results": discount_service.list_currencies(self.get_channel())})


class AdminChannelListView(_AdminView):
    @extend_schema(
        summary="List channels",
        description="All checkout channels — options for a rule's channels multi-select (the URL channel is auto-added on create).",
        tags=[_TAG],
        responses={200: ChannelListResponse},
    )
    def get(self, request, channel_idx, **kwargs):
        return Response({"results": discount_service.list_channels()})


# --- Discount rules ---


class AdminDiscountRuleListView(_AdminView):
    @extend_schema(
        operation_id="discount_rules_list",  # disambiguate from rule retrieve (both are GET on APIViews)
        summary="List discount rules",
        description="Paginated discount rules for the channel. Filter by status, modifier, target, or search.",
        tags=[_TAG],
        parameters=[
            OpenApiParameter(name="channel_idx", location="path", description="Channel identifier"),
            OpenApiParameter(name="is_active", type=bool, description="Filter by active status"),
            OpenApiParameter(name="automatic", type=bool, description="Filter by automatic application"),
            OpenApiParameter(name="modifier", description="Filter by modifier type"),
            OpenApiParameter(name="target", description="Filter by eligibility target"),
            OpenApiParameter(name="search", description="Search by rule name or code"),
            OpenApiParameter(
                name="ordering", description="created_at | -created_at | priority | -priority | name | -name"
            ),
            OpenApiParameter(name="page", type=int),
            OpenApiParameter(name="page_size", type=int),
        ],
        responses={200: DiscountRuleListResponse},
    )
    def get(self, request, channel_idx, **kwargs):
        qs = discount_service.list_rules(self.get_channel(), request.query_params)
        paginator = AdminPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        results = [discount_service.serialize_rule_list_item(rule) for rule in page]
        return paginator.get_paginated_response(results)

    @extend_schema(
        summary="Create discount rule",
        description="Create a rule. The URL channel is always added to the rule's channels. Optional nested codes.",
        tags=[_TAG],
        request=DiscountRuleCreateInput,
        responses={201: DiscountRuleDetailResponse},
    )
    def post(self, request, channel_idx, **kwargs):
        data = _read(DiscountRuleCreateInput, request)
        try:
            rule = discount_service.create_rule(self.get_channel(), data)
        except ValueError as exc:
            return _bad_request(str(exc))
        return Response(discount_service.serialize_rule_detail(rule), status=201)


class AdminDiscountRuleBulkView(_AdminView):
    @extend_schema(
        summary="Bulk action on discount rules",
        description="Activate, deactivate, or delete many rules at once — by `ids`, or ALL rules matching the list `filters` (across every page).",
        tags=[_TAG],
        request=DiscountRuleBulkRequest,
        responses={200: DiscountRuleBulkResponse},
    )
    def post(self, request, channel_idx, **kwargs):
        data = _read(DiscountRuleBulkRequest, request)
        try:
            affected = discount_service.bulk_rules(
                self.get_channel(),
                action=data.action,
                ids=data.ids,
                all_matching=data.all_matching,
                params=data.filters,
            )
        except ValueError as exc:
            return _bad_request(str(exc))
        return Response({"affected": affected})


class AdminDiscountRuleDetailView(_AdminView):
    @extend_schema(
        summary="Retrieve discount rule",
        description="Full rule with nested codes, product filters, threshold filters, and customer filters.",
        tags=[_TAG],
        responses={200: DiscountRuleDetailResponse},
    )
    def get(self, request, channel_idx, rule_id, **kwargs):
        try:
            rule = discount_service.get_rule(self.get_channel(), rule_id)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(discount_service.serialize_rule_detail(rule))

    @extend_schema(
        summary="Update discount rule",
        description="Partial update. Only provided fields change. Manage codes via the codes sub-resource.",
        tags=[_TAG],
        request=DiscountRuleUpdateInput,
        responses={200: DiscountRuleDetailResponse},
    )
    def patch(self, request, channel_idx, rule_id, **kwargs):
        data = _read(DiscountRuleUpdateInput, request)
        try:
            rule = discount_service.update_rule(self.get_channel(), rule_id, data)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        except ValueError as exc:
            return _bad_request(str(exc))
        return Response(discount_service.serialize_rule_detail(rule))

    @extend_schema(
        summary="Delete discount rule",
        description="Delete the rule and cascade its codes/filters.",
        tags=[_TAG],
        responses={204: None},
    )
    def delete(self, request, channel_idx, rule_id, **kwargs):
        try:
            discount_service.delete_rule(self.get_channel(), rule_id)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(status=204)


# --- Discount codes ---


class AdminDiscountCodeListView(_AdminView):
    @extend_schema(
        summary="List codes for rule",
        description="Paginated codes under the rule. Search by code, sort by code/usage/dates.",
        tags=[_TAG],
        parameters=[
            OpenApiParameter(name="search", description="Search by code (icontains)"),
            OpenApiParameter(
                name="ordering",
                description="code | current_used | max_used | max_uses_per_user | max_products_qty | active_from | active_to (prefix - for descending)",
            ),
            OpenApiParameter(name="page", type=int),
            OpenApiParameter(name="page_size", type=int),
        ],
        responses={200: DiscountCodeListResponse},
    )
    def get(self, request, channel_idx, rule_id, **kwargs):
        try:
            queryset = discount_service.list_codes(self.get_channel(), rule_id, request.query_params)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        paginator = AdminPageNumberPagination()
        page = paginator.paginate_queryset(queryset, request)
        return paginator.get_paginated_response([discount_service.serialize_code(code) for code in page])

    @extend_schema(
        summary="Create code",
        description="Add a coupon code to the rule.",
        tags=[_TAG],
        request=DiscountCodeInput,
        responses={201: DiscountCodeResponse},
    )
    def post(self, request, channel_idx, rule_id, **kwargs):
        data = _read(DiscountCodeInput, request)
        try:
            code = discount_service.create_code(self.get_channel(), rule_id, data)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(discount_service.serialize_code(code), status=201)


class AdminDiscountCodeDetailView(_AdminView):
    @extend_schema(
        summary="Update code",
        description="Partial update of a code. current_used is read-only.",
        tags=[_TAG],
        request=DiscountCodeUpdateInput,
        responses={200: DiscountCodeResponse},
    )
    def patch(self, request, channel_idx, rule_id, code_id, **kwargs):
        data = _read(DiscountCodeUpdateInput, request)
        try:
            code = discount_service.update_code(self.get_channel(), rule_id, code_id, data)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(discount_service.serialize_code(code))

    @extend_schema(
        summary="Delete code", description="Delete a code from the rule.", tags=[_TAG], responses={204: None}
    )
    def delete(self, request, channel_idx, rule_id, code_id, **kwargs):
        try:
            discount_service.delete_code(self.get_channel(), rule_id, code_id)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(status=204)


# --- Product / threshold filters (shared logic, distinct kind) ---


def _list_filters(view, rule_id, kind):
    try:
        filters = discount_service.list_product_filters(view.get_channel(), rule_id, kind)
    except ObjectDoesNotExist as exc:
        return _not_found(str(exc))
    return Response({"results": [discount_service.serialize_product_filter(f) for f in filters]})


def _create_filter(view, request, rule_id, kind):
    data = _read(ProductFilterInput, request)
    try:
        filter_obj = discount_service.create_product_filter(view.get_channel(), rule_id, data, kind)
    except ObjectDoesNotExist as exc:
        return _not_found(str(exc))
    except ValueError as exc:
        return _bad_request(str(exc))
    return Response(discount_service.serialize_product_filter(filter_obj), status=201)


def _update_filter(view, request, rule_id, filter_id, kind):
    data = _read(ProductFilterInput, request)
    try:
        filter_obj = discount_service.update_product_filter(view.get_channel(), rule_id, filter_id, data, kind)
    except ObjectDoesNotExist as exc:
        return _not_found(str(exc))
    except ValueError as exc:
        return _bad_request(str(exc))
    return Response(discount_service.serialize_product_filter(filter_obj))


def _delete_filter(view, rule_id, filter_id, kind):
    try:
        discount_service.delete_product_filter(view.get_channel(), rule_id, filter_id, kind)
    except ObjectDoesNotExist as exc:
        return _not_found(str(exc))
    return Response(status=204)


class AdminProductFilterListView(_AdminView):
    @extend_schema(
        summary="List product filters",
        description="Filters defining which products receive the discount / are gratis-eligible.",
        tags=[_TAG],
        responses={200: ProductFilterListResponse},
    )
    def get(self, request, channel_idx, rule_id, **kwargs):
        return _list_filters(self, rule_id, "product")

    @extend_schema(
        summary="Create product filter",
        description="Add a product eligibility filter to the rule.",
        tags=[_TAG],
        request=ProductFilterInput,
        responses={201: ProductFilterResponse},
    )
    def post(self, request, channel_idx, rule_id, **kwargs):
        return _create_filter(self, request, rule_id, "product")


class AdminProductFilterDetailView(_AdminView):
    @extend_schema(
        summary="Update product filter",
        description="Partial update of a product filter.",
        tags=[_TAG],
        request=ProductFilterInput,
        responses={200: ProductFilterResponse},
    )
    def patch(self, request, channel_idx, rule_id, filter_id, **kwargs):
        return _update_filter(self, request, rule_id, filter_id, "product")

    @extend_schema(
        summary="Delete product filter",
        description="Delete a product filter from the rule.",
        tags=[_TAG],
        responses={204: None},
    )
    def delete(self, request, channel_idx, rule_id, filter_id, **kwargs):
        return _delete_filter(self, rule_id, filter_id, "product")


class AdminThresholdFilterListView(_AdminView):
    @extend_schema(
        summary="List threshold filters",
        description="Filters defining which products count toward a gratis threshold.",
        tags=[_TAG],
        responses={200: ProductFilterListResponse},
    )
    def get(self, request, channel_idx, rule_id, **kwargs):
        return _list_filters(self, rule_id, "threshold")

    @extend_schema(
        summary="Create threshold filter",
        description="Add a gratis-threshold product filter to the rule.",
        tags=[_TAG],
        request=ProductFilterInput,
        responses={201: ProductFilterResponse},
    )
    def post(self, request, channel_idx, rule_id, **kwargs):
        return _create_filter(self, request, rule_id, "threshold")


class AdminThresholdFilterDetailView(_AdminView):
    @extend_schema(
        summary="Update threshold filter",
        description="Partial update of a threshold filter.",
        tags=[_TAG],
        request=ProductFilterInput,
        responses={200: ProductFilterResponse},
    )
    def patch(self, request, channel_idx, rule_id, filter_id, **kwargs):
        return _update_filter(self, request, rule_id, filter_id, "threshold")

    @extend_schema(
        summary="Delete threshold filter",
        description="Delete a threshold filter from the rule.",
        tags=[_TAG],
        responses={204: None},
    )
    def delete(self, request, channel_idx, rule_id, filter_id, **kwargs):
        return _delete_filter(self, rule_id, filter_id, "threshold")


# --- Customer filters ---


class AdminCustomerFilterListView(_AdminView):
    @extend_schema(
        summary="List customer filters",
        description="Customer/group targeting for the rule.",
        tags=[_TAG],
        responses={200: CustomerFilterListResponse},
    )
    def get(self, request, channel_idx, rule_id, **kwargs):
        try:
            filters = discount_service.list_customer_filters(self.get_channel(), rule_id)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response({"results": [discount_service.serialize_customer_filter(f) for f in filters]})

    @extend_schema(
        summary="Create customer filter",
        description="Target customers (by uid) or groups (by code).",
        tags=[_TAG],
        request=CustomerFilterInput,
        responses={201: CustomerFilterResponse},
    )
    def post(self, request, channel_idx, rule_id, **kwargs):
        data = _read(CustomerFilterInput, request)
        try:
            filter_obj = discount_service.create_customer_filter(self.get_channel(), rule_id, data)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        except ValueError as exc:
            return _bad_request(str(exc))
        return Response(discount_service.serialize_customer_filter(filter_obj), status=201)


class AdminCustomerFilterDetailView(_AdminView):
    @extend_schema(
        summary="Update customer filter",
        description="Partial update of a customer filter.",
        tags=[_TAG],
        request=CustomerFilterInput,
        responses={200: CustomerFilterResponse},
    )
    def patch(self, request, channel_idx, rule_id, filter_id, **kwargs):
        data = _read(CustomerFilterInput, request)
        try:
            filter_obj = discount_service.update_customer_filter(self.get_channel(), rule_id, filter_id, data)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        except ValueError as exc:
            return _bad_request(str(exc))
        return Response(discount_service.serialize_customer_filter(filter_obj))

    @extend_schema(
        summary="Delete customer filter",
        description="Delete a customer filter from the rule.",
        tags=[_TAG],
        responses={204: None},
    )
    def delete(self, request, channel_idx, rule_id, filter_id, **kwargs):
        try:
            discount_service.delete_customer_filter(self.get_channel(), rule_id, filter_id)
        except ObjectDoesNotExist as exc:
            return _not_found(str(exc))
        return Response(status=204)
