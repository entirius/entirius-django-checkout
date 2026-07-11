# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""v2 Cart API views — DRF APIViews with Pydantic request/response schemas.

Each view: parse Pydantic request -> call cart_service -> build_v2_cart_response -> return.
Every write operation returns full cart state (Decision #9).
"""

import logging

from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from pydantic import ValidationError as PydanticValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_checkout.api.v2.exceptions import pydantic_error_response
from django_checkout.api.v2.mixins import CheckoutChannelMixin
from django_checkout.api.v2.permissions import ChannelAPIKeyPermission
from django_checkout.models import Cart
from django_checkout.schemas.requests.cart import (
    AddressesPatchRequest,
    CartCreateRequest,
    CartMergeRequest,
    DiscountsPatchRequest,
    ItemsPatchRequest,
    PaymentPatchRequest,
    ShippingPatchRequest,
)
from django_checkout.schemas.responses.cart import CartV2Response
from django_checkout.services import cart_service
from django_checkout.services.cart_response_builder import build_v2_cart_response, build_v2_cart_response_fresh
from django_checkout.views.countries import _build_countries_response
from django_checkout.views.gratis import _build_gratis_response
from django_checkout.views.payment import _build_payment_methods_response
from django_checkout.views.shipping import _build_shipping_methods_response

logger = logging.getLogger("checkout.v2")


class CartCreateView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Create cart",
        description="Create a new cart with initial items and currency.",
        tags=["Checkout Cart"],
        request=CartCreateRequest,
        responses={201: CartV2Response},
    )
    def post(self, request, **kwargs):
        try:
            body = CartCreateRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        record, messages = cart_service.create_cart(
            channel=channel,
            items=[item.model_dump() for item in body.items],
            currency_code=body.currency_code,
            language_code=body.language_code,
            country_code=body.country_code,
            customer=customer,
            geo_country=geo_country,
        )
        return Response(build_v2_cart_response(record, messages), status=status.HTTP_201_CREATED)


class CartDetailView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Retrieve cart",
        description="Get cart by UUID.",
        tags=["Checkout Cart"],
        responses={200: CartV2Response},
    )
    def get(self, request, cart_id, **kwargs):
        record = self.get_cart_record(cart_id)
        return Response(build_v2_cart_response_fresh(record))

    @extend_schema(
        summary="Close cart",
        description="Close (deactivate) cart.",
        tags=["Checkout Cart"],
        responses={204: None},
    )
    def delete(self, request, cart_id, **kwargs):
        channel = self.get_channel()
        try:
            cart_service.close_cart(channel, cart_id)
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CartLatestView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission, IsAuthenticated]

    @extend_schema(
        summary="Retrieve latest cart",
        description="Get the most recent active cart for authenticated customer.",
        tags=["Checkout Cart"],
        responses={200: CartV2Response},
    )
    def get(self, request, **kwargs):
        channel = self.get_channel()
        customer = self.get_customer()
        record = cart_service.get_latest_cart(channel, customer)
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response_fresh(record))


class CartItemsView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Update cart items",
        description="Replace cart items. Preserves addresses, shipping, payment, discounts.",
        tags=["Checkout Cart"],
        request=ItemsPatchRequest,
        responses={200: CartV2Response},
    )
    def patch(self, request, cart_id, **kwargs):
        try:
            body = ItemsPatchRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.patch_items(
                channel=channel,
                cart_id=cart_id,
                items=[item.model_dump() for item in body.items],
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartAddressesView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Set addresses",
        description="Set billing and/or shipping address. May reset shipping method if country changes.",
        tags=["Checkout Cart"],
        request=AddressesPatchRequest,
        responses={200: CartV2Response},
    )
    def patch(self, request, cart_id, **kwargs):
        try:
            body = AddressesPatchRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.patch_addresses(
                channel=channel,
                cart_id=cart_id,
                billing=body.billing_address.model_dump() if body.billing_address else None,
                shipping=body.shipping_address.model_dump() if body.shipping_address else None,
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartDiscountsView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Apply discount codes",
        description="Apply, replace, or clear discount codes. Automatic rules always re-discovered.",
        tags=["Checkout Cart"],
        request=DiscountsPatchRequest,
        responses={200: CartV2Response},
    )
    def patch(self, request, cart_id, **kwargs):
        try:
            body = DiscountsPatchRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.patch_discounts(
                channel=channel,
                cart_id=cart_id,
                codes=[c.model_dump() for c in body.codes],
                clear=body.clear,
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartShippingMethodsView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="List available shipping methods",
        description="Returns shipping methods available for cart (based on address country, items, weight).",
        tags=["Checkout Cart"],
    )
    def get(self, request, cart_id, **kwargs):
        record = self.get_cart_record(cart_id)
        return Response(_build_shipping_methods_response(record))


class CartShippingView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Select shipping method",
        description="Select a shipping method for the cart. Optionally include delivery point.",
        tags=["Checkout Cart"],
        request=ShippingPatchRequest,
        responses={200: CartV2Response},
    )
    def patch(self, request, cart_id, **kwargs):
        try:
            body = ShippingPatchRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.patch_shipping(
                channel=channel,
                cart_id=cart_id,
                code=body.code,
                delivery_point=body.delivery_point.model_dump() if body.delivery_point else None,
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartPaymentMethodsView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="List available payment methods",
        description="Returns payment methods available for cart (based on address country, currency, products).",
        tags=["Checkout Cart"],
    )
    def get(self, request, cart_id, **kwargs):
        record = self.get_cart_record(cart_id)
        return Response(_build_payment_methods_response(record))


class CartPaymentView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="Select payment method",
        description="Select a payment method. Optionally include bank ID, card token.",
        tags=["Checkout Cart"],
        request=PaymentPatchRequest,
        responses={200: CartV2Response},
    )
    def patch(self, request, cart_id, **kwargs):
        try:
            body = PaymentPatchRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.patch_payment(
                channel=channel,
                cart_id=cart_id,
                code=body.code,
                bank_id=body.bank_id,
                card=body.card,
                save_card=body.save_card,
                pay_code=body.pay_code,
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartMergeView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission, IsAuthenticated]

    @extend_schema(
        summary="Merge guest cart",
        description="Merge a guest cart into authenticated customer's cart. Guest cart is deleted.",
        tags=["Checkout Cart"],
        request=CartMergeRequest,
        responses={200: CartV2Response},
    )
    def post(self, request, cart_id, **kwargs):
        try:
            body = CartMergeRequest(**request.data)
        except PydanticValidationError as exc:
            return pydantic_error_response(exc)

        channel = self.get_channel()
        customer = self.get_customer()
        geo_country = request.headers.get("X-Country", None)

        try:
            record, messages = cart_service.merge_carts(
                channel=channel,
                target_cart_id=cart_id,
                guest_cart_id=body.guest_cart_id,
                operation=body.operation,
                customer=customer,
                geo_country=geo_country,
            )
        except Cart.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(build_v2_cart_response(record, messages))


class CartGratisView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="List gratis rules",
        description="Returns available free product rules for the cart.",
        tags=["Checkout Cart"],
    )
    def get(self, request, cart_id, **kwargs):
        record = self.get_cart_record(cart_id)
        return Response(_build_gratis_response(record, customer=self.get_customer()))


class CountriesView(CheckoutChannelMixin, APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [ChannelAPIKeyPermission]

    @extend_schema(
        summary="List countries",
        description="Returns available countries for the channel.",
        tags=["Checkout Cart"],
        parameters=[
            OpenApiParameter(
                name="language",
                description="Language ISO2 for country labels",
                examples=[OpenApiExample(name="english", value="en")],
            ),
        ],
    )
    def get(self, request, **kwargs):
        channel = self.get_channel()
        language = request.query_params.get("language")
        return Response(_build_countries_response(channel, language))
