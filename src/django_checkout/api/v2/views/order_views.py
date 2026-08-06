# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Order API views — create, list, detail, attachments."""

import logging
import os

from django.core.exceptions import ObjectDoesNotExist
from django.http import FileResponse
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_checkout.api.v2.mixins import CheckoutChannelMixin
from django_checkout.api.v2.permissions import ChannelAPIKeyPermission
from django_checkout.models import Order, OrderAttachment
from django_checkout.schemas.responses.order import OrderCreateResponse, OrderListResponse
from django_checkout.services import order_service
from django_checkout.utils import redact_payment_secrets

logger = logging.getLogger("checkout.v2.orders")


class OrderCreateView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Create order",
        description="Finalize cart into order. Validates everything, reserves stock, initiates payment.",
        tags=["Checkout Orders"],
        responses={201: OrderCreateResponse},
    )
    def post(self, request, **kwargs):
        channel = self.get_channel()
        customer = self.get_customer()
        cart_id = request.data.get("cart_id")
        if not cart_id:
            return Response({"error": "VALIDATION_ERROR", "message": "cart_id is required."}, status=400)

        try:
            result = order_service.create_order(
                channel,
                cart_id,
                customer=customer,
                geo_country=request.headers.get("X-Country"),
                request=request,
            )
        except ObjectDoesNotExist:
            return Response({"error": "NOT_FOUND", "message": "Cart does not exist."}, status=404)

        response = OrderCreateResponse(
            order_id=str(result.order.order_id) if result.order else "",
            order_pretty_id=result.order.pretty_id if result.order else "",
            order_status=result.order.order_status if result.order else "",
            redirect_url=result.redirect_url,
            split_orders_pretty_ids=result.split_pretty_ids,
        )
        data = response.model_dump()
        if result.payment_failed:
            data["payment_error"] = True
        return Response(data, status=status.HTTP_201_CREATED)


class OrderListView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission, IsAuthenticated]

    @extend_schema(
        summary="List customer orders",
        description="Returns paginated list of orders for authenticated customer.",
        tags=["Checkout Orders"],
        parameters=[
            OpenApiParameter(name="status", description="Filter by order status"),
            OpenApiParameter(name="order_id", description="Search by pretty_id or UUID"),
            OpenApiParameter(name="page", description="Page number", type=int),
            OpenApiParameter(name="page_size", description="Items per page", type=int),
            OpenApiParameter(
                name="ordering",
                description="Sort field",
                examples=[OpenApiExample(name="created_desc", value="-created")],
            ),
            OpenApiParameter(
                name="language",
                description="Language for status and payment method labels (default: channel language)",
                examples=[OpenApiExample(name="english", value="en")],
            ),
        ],
        responses={200: OrderListResponse},
    )
    def get(self, request, **kwargs):
        channel = self.get_channel()
        customer = self.get_customer()
        if customer is None:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        results, total, next_url, prev_url = order_service.list_customer_orders(
            channel,
            customer,
            request.query_params,
        )
        return Response(
            OrderListResponse(
                count=total,
                next=next_url,
                previous=prev_url,
                results=results,
            ).model_dump()
        )


class OrderDetailView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission, IsAuthenticated]

    @extend_schema(
        summary="Retrieve order detail",
        description="Returns full order with cart snapshot.",
        tags=["Checkout Orders"],
    )
    def get(self, request, pretty_id, **kwargs):
        channel = self.get_channel()
        customer = self.get_customer()
        # SECURITY: customer=None matches every GUEST order, and pretty_id is sequential —
        # without this an authenticated user holding no Customer row could enumerate them.
        if customer is None:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        try:
            order = Order.objects.get(
                order_service.order_lookup_q(pretty_id),
                channel=channel,
                customer=customer,
            )
        except Order.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        attachments = [{"file_id": a.pk, "name": a.name} for a in OrderAttachment.objects.filter(order=order)]
        return Response(
            {
                "order_id": str(order.order_id),
                "pretty_id": order.pretty_id or "",
                "status": order.order_status,
                "created": order.created.isoformat() if order.created else None,
                "updated": order.updated.isoformat() if order.updated else None,
                "order_body": redact_payment_secrets(order.order_body),
                "attachments": attachments,
            }
        )


class OrderAttachmentView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission, IsAuthenticated]

    @extend_schema(
        summary="Download order attachment",
        description="Download a file attachment for an order.",
        tags=["Checkout Orders"],
    )
    def get(self, request, order_id, file_id, **kwargs):
        channel = self.get_channel()
        customer = self.get_customer()
        # SECURITY: see OrderDetailView — customer=None would expose every guest invoice.
        if customer is None:
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        try:
            order = Order.objects.get(order_service.order_lookup_q(order_id), channel=channel, customer=customer)
            attachment = OrderAttachment.objects.get(pk=file_id, order=order)
        except (Order.DoesNotExist, OrderAttachment.DoesNotExist):
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not attachment.attachment or not os.path.exists(attachment.attachment.path):
            return Response(status=status.HTTP_404_NOT_FOUND)

        return FileResponse(
            open(attachment.attachment.path, "rb"),
            content_type="application/octet-stream",
            as_attachment=True,
            filename=attachment.name or os.path.basename(attachment.attachment.path),
        )
