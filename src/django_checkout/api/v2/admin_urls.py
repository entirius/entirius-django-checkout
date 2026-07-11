# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.urls import path

from django_checkout.api.v2.views.admin.discount_views import (
    AdminChannelListView,
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
    AdminThresholdFilterDetailView,
    AdminThresholdFilterListView,
)
from django_checkout.api.v2.views.admin.order_views import (
    AdminOrderAttachmentView,
    AdminOrderCancelView,
    AdminOrderDetailView,
    AdminOrderListView,
    AdminOrderStatusView,
)

urlpatterns = [
    path("orders/", AdminOrderListView.as_view(), name="checkout-v2-admin-orders"),
    path("orders/<str:uid>/", AdminOrderDetailView.as_view(), name="checkout-v2-admin-orders-detail"),
    path("orders/<str:uid>/status/", AdminOrderStatusView.as_view(), name="checkout-v2-admin-orders-status"),
    path("orders/<str:uid>/cancel/", AdminOrderCancelView.as_view(), name="checkout-v2-admin-orders-cancel"),
    path(
        "orders/<str:uid>/attachments/", AdminOrderAttachmentView.as_view(), name="checkout-v2-admin-orders-attachments"
    ),
    # Discount form metadata (modifier/target/inclusion choices + extra_value hints)
    path("discount-meta/", AdminDiscountMetaView.as_view(), name="checkout-v2-admin-discount-meta"),
    # Shipping methods for the channel (options for free_shipping_methods multi-select)
    path("shipping-methods/", AdminShippingMethodListView.as_view(), name="checkout-v2-admin-shipping-methods"),
    # Currencies for the channel (options for currencies multi-select + per-currency thresholds)
    path("currencies/", AdminCurrencyListView.as_view(), name="checkout-v2-admin-currencies"),
    # Channels (options for the rule's channels multi-select)
    path("channels/", AdminChannelListView.as_view(), name="checkout-v2-admin-channels"),
    # Discount rules (aggregate root)
    path("discount-rules/", AdminDiscountRuleListView.as_view(), name="checkout-v2-admin-discount-rules"),
    path("discount-rules/bulk/", AdminDiscountRuleBulkView.as_view(), name="checkout-v2-admin-discount-rules-bulk"),
    path(
        "discount-rules/<int:rule_id>/",
        AdminDiscountRuleDetailView.as_view(),
        name="checkout-v2-admin-discount-rules-detail",
    ),
    # Codes
    path(
        "discount-rules/<int:rule_id>/codes/",
        AdminDiscountCodeListView.as_view(),
        name="checkout-v2-admin-discount-codes",
    ),
    path(
        "discount-rules/<int:rule_id>/codes/<int:code_id>/",
        AdminDiscountCodeDetailView.as_view(),
        name="checkout-v2-admin-discount-codes-detail",
    ),
    # Product filters (which products get the discount / are gratis-eligible)
    path(
        "discount-rules/<int:rule_id>/product-filters/",
        AdminProductFilterListView.as_view(),
        name="checkout-v2-admin-discount-product-filters",
    ),
    path(
        "discount-rules/<int:rule_id>/product-filters/<int:filter_id>/",
        AdminProductFilterDetailView.as_view(),
        name="checkout-v2-admin-discount-product-filters-detail",
    ),
    # Threshold filters (which products count toward gratis threshold)
    path(
        "discount-rules/<int:rule_id>/threshold-filters/",
        AdminThresholdFilterListView.as_view(),
        name="checkout-v2-admin-discount-threshold-filters",
    ),
    path(
        "discount-rules/<int:rule_id>/threshold-filters/<int:filter_id>/",
        AdminThresholdFilterDetailView.as_view(),
        name="checkout-v2-admin-discount-threshold-filters-detail",
    ),
    # Customer filters (customer/group targeting)
    path(
        "discount-rules/<int:rule_id>/customer-filters/",
        AdminCustomerFilterListView.as_view(),
        name="checkout-v2-admin-discount-customer-filters",
    ),
    path(
        "discount-rules/<int:rule_id>/customer-filters/<int:filter_id>/",
        AdminCustomerFilterDetailView.as_view(),
        name="checkout-v2-admin-discount-customer-filters-detail",
    ),
]
