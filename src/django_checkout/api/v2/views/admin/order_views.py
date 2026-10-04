# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Admin Order API — order management for CMS/Hugin.

JWT + IsAdminUser auth. Paginated list with filters.
"""

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_checkout.api.v2.mixins import CheckoutChannelMixin
from django_checkout.api.v2.pagination import AdminPageNumberPagination
from django_checkout.enums import OrderStatus
from django_checkout.models import Order, OrderAttachment, OrderStatusLabel, StockReservation
from django_checkout.schemas.common import format_money
from django_checkout.services.order_service import order_lookup_q
from django_checkout.utils import redact_payment_secrets

logger = logging.getLogger("checkout.v2.admin")

ALLOWED_STATUS_TRANSITIONS = {
    OrderStatus.UNPAID: [OrderStatus.CONFIRMED, OrderStatus.CANCELED],
    OrderStatus.NEW: [OrderStatus.CONFIRMED, OrderStatus.CANCELED],
    OrderStatus.CONFIRMED: [OrderStatus.IN_PROGRESS, OrderStatus.CANCELED, OrderStatus.RETURNED],
    OrderStatus.HOLDED: [OrderStatus.CONFIRMED, OrderStatus.CANCELED],
    OrderStatus.IN_PROGRESS: [OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED],
    OrderStatus.COMPLETED: [OrderStatus.RETURNED],
    OrderStatus.RETURNED: [],
    OrderStatus.CANCELED: [],
}


def _get_order(channel, uid: str) -> Order:
    """Fetch order by pretty_id or UUID. Raises Order.DoesNotExist."""
    return Order.objects.select_related("customer__user", "cart").get(
        order_lookup_q(uid),
        channel=channel,
    )


class AdminOrderListView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.orders"

    @extend_schema(
        summary="List orders (admin)",
        description="Paginated order list with filters for CMS panel.",
        tags=["Checkout Admin"],
        parameters=[
            OpenApiParameter(name="status", description="Filter by order status"),
            OpenApiParameter(name="email", description="Search by billing/shipping email"),
            OpenApiParameter(name="order_id", description="Search by pretty_id or UUID"),
            OpenApiParameter(name="date_from", description="Created after (ISO date)"),
            OpenApiParameter(name="date_to", description="Created before (ISO date)"),
            OpenApiParameter(name="ordering", description="Sort field"),
            OpenApiParameter(name="page", type=int),
            OpenApiParameter(name="page_size", type=int),
        ],
    )
    def get(self, request, channel_idx, **kwargs):
        channel = self.get_channel()
        qs = _build_admin_order_queryset(channel, request.query_params)

        paginator = AdminPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)

        labels = {(sl.status, sl.channel_id): sl.name_t9n for sl in OrderStatusLabel.objects.filter(channel=channel)}

        results = [_serialize_order_list_item(order, channel, labels) for order in page]
        return paginator.get_paginated_response(results)


class AdminOrderDetailView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.orders"

    @extend_schema(
        summary="Retrieve order detail (admin)",
        description="Full order with body, payment intents, shipping intents, attachments.",
        tags=["Checkout Admin"],
    )
    def get(self, request, channel_idx, uid, **kwargs):
        try:
            order = _get_order(self.get_channel(), uid)
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # SECURITY: audit log for PII access (GDPR)
        logger.info("Admin %s accessed order detail %s (email: %s)", request.user, uid, order.billing_email)

        return Response(
            {
                "order_id": str(order.order_id),
                "pretty_id": order.pretty_id or "",
                "status": order.order_status,
                "created": order.created,
                "updated": order.updated,
                "billing_email": order.billing_email,
                "shipping_email": order.shipping_email,
                "customer_uid": str(order.customer.user.username) if order.customer else None,
                "order_body": redact_payment_secrets(order.order_body),
                "payment_intents": [
                    {"code": pi.code, "status": pi.payment_status, "external_order_id": pi.external_order_id}
                    for pi in order.payment_items.all()
                ],
                "shipping_intents": [
                    {"code": si.code, "tracking_number": si.tracking_number, "tracking_link": si.tracking_link}
                    for si in order.shipping_items.all()
                ],
                "attachments": [{"file_id": a.pk, "name": a.name} for a in order.orderattachment_set.all()],
                "extra": order.extra,
            }
        )


class AdminOrderStatusView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.orders"

    @extend_schema(
        summary="Update order status",
        description="Validate status transition and update.",
        tags=["Checkout Admin"],
    )
    def patch(self, request, channel_idx, uid, **kwargs):
        new_status = request.data.get("status")
        if not new_status:
            return Response({"error": "VALIDATION_ERROR", "message": "status is required."}, status=400)

        try:
            order = _get_order(self.get_channel(), uid)
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        allowed = ALLOWED_STATUS_TRANSITIONS.get(order.order_status, [])
        if new_status not in allowed:
            return Response(
                {
                    "error": "VALIDATION_ERROR",
                    "message": "Invalid status transition.",
                    "details": [{"allowed_transitions": allowed}],
                },
                status=400,
            )

        order.modify_status(new_status)
        order.save()
        return Response(
            {"order_id": str(order.order_id), "pretty_id": order.pretty_id or "", "status": order.order_status}
        )


class AdminOrderCancelView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.orders"

    @extend_schema(summary="Cancel order", description="Cancel order and release stock.", tags=["Checkout Admin"])
    def post(self, request, channel_idx, uid, **kwargs):
        try:
            order = _get_order(self.get_channel(), uid)
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if order.order_status == OrderStatus.CANCELED:
            return Response({"message": "Order already canceled."})

        allowed = ALLOWED_STATUS_TRANSITIONS.get(order.order_status, [])
        if OrderStatus.CANCELED not in allowed:
            return Response({"error": "VALIDATION_ERROR", "message": "Cannot cancel order in this status."}, status=400)

        StockReservation.objects.filter(order=order).delete()
        order.modify_status(OrderStatus.CANCELED)
        order.save()
        return Response(
            {"order_id": str(order.order_id), "pretty_id": order.pretty_id or "", "status": order.order_status}
        )


class AdminOrderAttachmentView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "checkout.orders"
    access_levels = {"GET": "write"}

    @extend_schema(summary="List/upload order attachments", tags=["Checkout Admin"])
    def get(self, request, channel_idx, uid, **kwargs):
        try:
            order = _get_order(self.get_channel(), uid)
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response([{"file_id": a.pk, "name": a.name} for a in order.orderattachment_set.all()])

    @extend_schema(summary="Upload order attachment", tags=["Checkout Admin"])
    def post(self, request, channel_idx, uid, **kwargs):
        try:
            order = _get_order(self.get_channel(), uid)
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
        ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "text/plain", "text/csv"}

        file = request.FILES.get("file")
        if not file:
            return Response({"error": "VALIDATION_ERROR", "message": "file is required."}, status=400)
        if file.size > MAX_UPLOAD_SIZE:
            return Response({"error": "VALIDATION_ERROR", "message": "File exceeds 10MB limit."}, status=413)
        if file.content_type not in ALLOWED_TYPES:
            return Response(
                {"error": "VALIDATION_ERROR", "message": "File type not allowed. Accepted: PDF, JPEG, PNG, TXT, CSV."},
                status=400,
            )

        attachment = OrderAttachment(order=order, name=request.data.get("name", file.name), attachment=file)
        attachment.save()
        return Response({"file_id": attachment.pk, "name": attachment.name}, status=status.HTTP_201_CREATED)


# --- Helpers ---


def _build_admin_order_queryset(channel, params):
    """Build filtered, sorted queryset for admin order list."""
    from django.db.models import Q

    qs = Order.objects.filter(channel=channel).select_related("customer__user").order_by("-created")

    if s := params.get("status"):
        qs = qs.filter(order_status=s)
    if email := params.get("email"):
        qs = qs.filter(Q(billing_email__icontains=email) | Q(shipping_email__icontains=email))
    if oid := params.get("order_id"):
        qs = qs.filter(Q(pretty_id_snap__icontains=oid) | Q(order_id__icontains=oid))
    if d := params.get("date_from"):
        qs = qs.filter(created__date__gte=d)
    if d := params.get("date_to"):
        qs = qs.filter(created__date__lte=d)

    ordering = params.get("ordering", "-created")
    allowed = {"created", "-created", "updated", "-updated", "order_status", "-order_status"}
    if ordering in allowed:
        qs = qs.order_by(ordering)
    return qs


def _serialize_order_list_item(order, channel, labels):
    """Serialize a single order for admin list response."""
    body = order.order_body or {}
    label_t9n = labels.get((order.order_status, channel.pk), {})
    return {
        "order_id": str(order.order_id),
        "pretty_id": order.pretty_id or "",
        "status": order.order_status,
        "status_label": label_t9n.get("en", order.order_status),
        "created": order.created,
        "updated": order.updated,
        "total_gross": format_money(body.get("total")),
        "currency": body.get("currency_code"),
        "item_count": len(body.get("cart", {}).get("items", [])),
        "billing_email": order.billing_email,
        "customer_uid": str(order.customer.user.username) if order.customer else None,
    }
