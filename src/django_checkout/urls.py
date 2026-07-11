# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.urls import include, path

from django_checkout import settings
from django_checkout.views import admin
from django_checkout.views.cart import get_latest_cart, get_or_put_cart, merge_cart, post_cart
from django_checkout.views.countries import listing_view
from django_checkout.views.gratis import get_gratis_for_cart
from django_checkout.views.order import get_order_attachment, order_view
from django_checkout.views.payment import get_payment_methods_for_cart
from django_checkout.views.payment_provider.autopay import autopay_return
from django_checkout.views.payment_provider.paynow import paynow_notify
from django_checkout.views.payment_provider.paypal import paypal_cancel, paypal_notify, paypal_return
from django_checkout.views.payment_provider.payu import payu_notify
from django_checkout.views.payment_provider.przelewy24 import przelewy24_notify
from django_checkout.views.payment_provider.stripe import stripe_notify
from django_checkout.views.shipping import get_shipping_methods_for_cart

api_paths = [
    path("customer/cart/", get_latest_cart, name="cart"),
    path("carts/", post_cart, name="carts"),
    path("carts/<str:cart_id>/", get_or_put_cart, name="carts-detail"),
    path("carts/<str:cart_id>/shipping-methods/", get_shipping_methods_for_cart, name="carts-detail-shipping-methods"),
    path("carts/<str:cart_id>/payment-methods/", get_payment_methods_for_cart, name="carts-detail-payment-methods"),
    path("carts/<str:cart_id>/gratis-rules/", get_gratis_for_cart, name="carts-detail-gratis-rules"),
    path("carts/<str:quest_cart_id>/merge/", merge_cart, name="carts-merge"),
    path("orders/", order_view, name="orders"),
    path("orders/<str:uid>/", order_view, name="orders-detail"),
    path("orders/<str:order_id>/file/<str:file_id>/customer/<str:uid>", get_order_attachment, name="order-attachment"),
    path("notify/payu/", payu_notify, name="payu-notify"),
    path("notify/paynow/", paynow_notify, name="paynow-notify"),
    path("notify/paypal/", paypal_notify, name="paypal-notify"),
    path("cancel/paypal/", paypal_cancel, name="paypal-cancel"),
    path("return/paypal/", paypal_return, name="paypal-return"),
    path("countries/", listing_view, name="get-countries"),
    path("notify/autopay/", autopay_return, name="autopay-return"),
    path("notify/przelewy24/", przelewy24_notify, name="przelewy24-notify"),
    path("notify/stripe/", stripe_notify, name="stripe-notify"),
]

admin_paths = [path("customer/delete", admin.admin_customer_delete, name="checkout-admin-customer-delete")]

urlpatterns = [
    # v2 (admin + public)
    path(
        f"{settings.PUBLIC_BASE_URL}/checkout/v2/admin/<str:channel_idx>/", include("django_checkout.api.v2.admin_urls")
    ),
    path(f"{settings.PUBLIC_BASE_URL}/checkout/v2/<str:channel_idx>/", include("django_checkout.api.v2.urls")),
    # v1 (unchanged)
    path(f"{settings.PUBLIC_BASE_URL}/checkout/<str:version>/<str:channel_idx>/", include(api_paths)),
    path(f"{settings.ADMIN_BASE_URL}/checkout/<str:version>/<str:channel_idx>/", include(admin_paths)),
]
