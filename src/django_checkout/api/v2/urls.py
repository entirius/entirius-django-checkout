# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.urls import path

from django_checkout.api.v2.views.cart_views import (
    CartAddressesView,
    CartCreateView,
    CartDetailView,
    CartDiscountsView,
    CartGratisView,
    CartItemsView,
    CartLatestView,
    CartMergeView,
    CartPaymentMethodsView,
    CartPaymentView,
    CartShippingMethodsView,
    CartShippingView,
    CountriesView,
)
from django_checkout.api.v2.views.order_views import (
    OrderAttachmentView,
    OrderCreateView,
    OrderDetailView,
    OrderListView,
)

urlpatterns = [
    # Cart CRUD
    path("carts/", CartCreateView.as_view(), name="checkout-v2-carts"),
    path("carts/latest/", CartLatestView.as_view(), name="checkout-v2-carts-latest"),
    path("carts/<str:cart_id>/", CartDetailView.as_view(), name="checkout-v2-carts-detail"),
    # Sub-resource PATCHes
    path("carts/<str:cart_id>/items/", CartItemsView.as_view(), name="checkout-v2-carts-items"),
    path("carts/<str:cart_id>/addresses/", CartAddressesView.as_view(), name="checkout-v2-carts-addresses"),
    path("carts/<str:cart_id>/discounts/", CartDiscountsView.as_view(), name="checkout-v2-carts-discounts"),
    path(
        "carts/<str:cart_id>/shipping-methods/", CartShippingMethodsView.as_view(), name="checkout-v2-shipping-methods"
    ),
    path("carts/<str:cart_id>/shipping/", CartShippingView.as_view(), name="checkout-v2-carts-shipping"),
    path("carts/<str:cart_id>/payment-methods/", CartPaymentMethodsView.as_view(), name="checkout-v2-payment-methods"),
    path("carts/<str:cart_id>/payment/", CartPaymentView.as_view(), name="checkout-v2-carts-payment"),
    path("carts/<str:cart_id>/merge/", CartMergeView.as_view(), name="checkout-v2-carts-merge"),
    path("carts/<str:cart_id>/gratis-rules/", CartGratisView.as_view(), name="checkout-v2-carts-gratis"),
    # Countries
    path("countries/", CountriesView.as_view(), name="checkout-v2-countries"),
    # Orders
    path("orders/", OrderCreateView.as_view(), name="checkout-v2-orders-create"),
    path("orders/list/", OrderListView.as_view(), name="checkout-v2-orders-list"),
    path("orders/<str:pretty_id>/", OrderDetailView.as_view(), name="checkout-v2-orders-detail"),
    path(
        "orders/<str:order_id>/files/<str:file_id>/", OrderAttachmentView.as_view(), name="checkout-v2-order-attachment"
    ),
]
