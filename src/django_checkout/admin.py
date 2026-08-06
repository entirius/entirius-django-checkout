# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import json
import logging

import pytz
from django import forms
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.db.models import Count, F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import path
from django.utils import timezone
from django.utils.safestring import mark_safe
from django_admin_inline_paginator.admin import TabularInline, TabularInlinePaginated

from django_checkout.models import (
    APIAdminKey,
    APIKey,
    Cart,
    Channel,
    CustomsThresholdConfig,
    DiscountCode,
    DiscountCustomerModeOfAction,
    DiscountModeOfAction,
    DiscountRuleCode,
    Invoice,
    Item,
    LinkedProducts,
    Order,
    OrderAttachment,
    OrderStatusLabel,
    PaymentIntent,
    PaymentMethod,
    PaymentRedirect,
    ProductRepresentation,
    SaleOffer,
    SaleOfferPrice,
    Shipping,
    ShippingIntent,
    ShippingMethod,
    ShippingOption,
    ShippingPriceByWeight,
    ShippingPriceMatrix,
    SplitOrderLink,
    Supplier,
    SupplierCustomer,
    ThresholdProductFilter,
    UsedCoupon,
)
from django_checkout.models.limited_products import LimitedProducts
from django_checkout.models.stock import Stock
from django_checkout.models.stock_reservation import StockReservation
from django_checkout.signals import order_additional_info
from django_checkout.worker.export_discount_rules import export_discount_rules_to_csv
from django_checkout.worker.import_discount_codes import DiscountCodeImportService
from django_checkout.worker.import_discount_rules import import_discount_rules_from_csv

logger = logging.getLogger("process")

logger = logging.getLogger("process")


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    model = APIKey
    list_display = ["channel", "key"]
    list_filter = ("channel",)


@admin.register(APIAdminKey)
class APIAdminKeyAdmin(admin.ModelAdmin):
    model = APIAdminKey
    list_display = ["channel", "key"]
    list_filter = ("channel",)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    model = Item
    list_display = ["sku", "quantity", "unit_price", "total_price", "preorder", "preorder_date"]
    search_fields = ["order__order_id", "sku"]
    list_filter = ["order__channel", "preorder"]
    autocomplete_fields = ["order", "product"]


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    model = Channel
    list_display = [
        "idx",
        "label",
        "default_language",
        "default_currency",
        "default_country",
        "min_order_price",
        "discount_apply_type",
        "order_pretty_id_prefix",
    ]
    search_fields = ["idx", "label"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "idx",
                    "label",
                    "default_language",
                    "default_currency",
                    "default_country",
                    "min_order_price",
                    "discount_apply_type",
                    "order_pretty_id_prefix",
                    "pretty_id_is_random_right_part",
                    "discount_mode_of_action_price_restriction_type",
                    "show_product_prices_when_invalid",
                )
            },
        ),
        (
            "(Optional) Split orders config",
            {
                "description": "Fill feature_idxs_for_split_order field to split orders by attribute.",
                "fields": (
                    "feature_idxs_for_split_order",
                    "default_attr_split",
                    "force_splitting_order_in_checkout",
                    "split_order_shipping_cost_mechanism",
                    "default_attr_split_shipping",
                    "split_order_payment_fee_mechanism",
                    "default_attr_split_payment_fee",
                    "split_order_mechanism",
                    "feature_separate_to_other_order",
                ),
            },
        ),
        (
            "(Optional) Preorder config",
            {
                "description": "Fill feature_idx to mark orders and items as preorders",
                "fields": ("preorder_feature_idx", "preorder_feature_value", "preorder_date_feature_idx"),
            },
        ),
    )


class ItemInline(admin.TabularInline):
    model = Item
    fields = ["sku", "quantity", "unit_price", "total_price"]
    readonly_fields = ["sku", "quantity", "unit_price", "total_price"]


class ShipmentItemInline(admin.TabularInline):
    model = Shipping
    fields = ["code", "total_price"]
    readonly_fields = ["code", "total_price"]


@admin.register(SplitOrderLink)
class SplitOrderLinkAdmin(admin.ModelAdmin):
    model = SplitOrderLink
    autocomplete_fields = ("original_order", "original_cart", "split_cart", "split_order")
    search_fields = [
        "original_order__order_id",
        "original_cart__cart_id",
        "split_cart__cart_id",
        "split_order__order_id",
    ]

    list_display = ["split_by_attribute_value", "split_by_feature_idx", "created"]
    autocomplete_fields = ["original_order", "original_cart", "split_cart", "split_order"]


class OrderSplitInline(TabularInlinePaginated):
    model = SplitOrderLink
    fk_name = "original_order"
    readonly_fields = ["created", "updated", "original_order", "original_cart", "split_cart", "split_order"]
    extra = 0
    ordering = ("-updated",)
    autocomplete_fields = ["original_order", "original_cart", "split_cart", "split_order"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    model = Order
    inlines = [OrderSplitInline]
    list_display = [
        "order_id",
        "pretty_id",
        "order_status",
        "in_status_since",
        "channel",
        "customer",
        "billing_email",
        "shipping_email",
        "cart",
        "preorder",
        "preorder_date",
        "created",
        "updated",
    ]
    list_filter = ["channel", "order_status", "preorder"]
    search_fields = ["order_id", "in_channel_id", "pretty_id_snap", "billing_email", "shipping_email"]
    fields = [
        "channel",
        "cart",
        "customer",
        "billing_email",
        "shipping_email",
        "order_status",
        "in_status_since",
        "order_id",
        "in_channel_id",
        "extra",
        "pretty_id",
        "preorder",
        "preorder_date",
        "data_prettified",
        "created",
        "updated",
    ]
    readonly_fields = [
        "channel",
        "cart",
        "billing_email",
        "shipping_email",
        "in_status_since",
        "order_id",
        "in_channel_id",
        "data_prettified",
        "pretty_id",
        "created",
        "updated",
    ]
    autocomplete_fields = ["channel", "customer", "cart"]

    def pretty_id(self, obj):
        return obj.pretty_id

    def data_prettified(self, instance):
        if len(instance.order_body) > 10000:
            return "Too much data, not formatting."
        response = json.dumps(instance.order_body, sort_keys=False, indent=4)
        return mark_safe(f"<pre>{response}</pre>")

    def has_add_permission(self, request, obj=None):
        return False

    data_prettified.allow_tags = True
    data_prettified.short_description = "data prettified"


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    model = Cart
    list_display = ["cart_id", "channel", "customer", "cart_status", "in_status_since", "created", "updated"]
    list_filter = ["channel", "cart_status"]
    search_fields = ["cart_id"]
    fields = ["channel", "customer", "cart_status", "in_status_since", "cart_id", "data_prettified"]
    readonly_fields = ["channel", "customer", "cart_status", "in_status_since", "cart_id", "data_prettified"]

    def data_prettified(self, instance):
        if len(instance.cart_body) > 10000:
            return "Too much data, not formating."
        response = json.dumps(instance.cart_body, sort_keys=False, indent=4)
        return mark_safe(f"<pre>{response}</pre>")

    def has_add_permission(self, request, obj=None):
        return False

    data_prettified.allow_tags = True
    data_prettified.short_description = "data prettified"


class DiscountCodeInline(TabularInlinePaginated):
    model = DiscountCode
    fields = ["code", "max_used", "max_uses_per_user", "current_used", "active_from", "active_to", "max_products_qty"]
    per_page = 100
    extra = 0


class MassEditDiscountCodeForm(forms.Form):
    """Form for mass editing DiscountCode fields"""

    max_used = forms.IntegerField(
        required=False, label="Max Used", help_text="Zostaw puste aby nie zmieniać", min_value=0
    )
    max_uses_per_user = forms.IntegerField(
        required=False,
        label="Max Uses Per User",
        help_text="Zostaw puste aby nie zmieniać (maksymalna liczba użyć na użytkownika)",
        min_value=0,
    )
    max_products_qty = forms.IntegerField(
        required=False, label="Max Products Qty", help_text="Zostaw puste aby nie zmieniać", min_value=0
    )
    active_from = forms.DateField(
        required=False,
        label="Active From",
        help_text="Zostaw puste aby nie zmieniać (format: YYYY-MM-DD)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    active_to = forms.DateField(
        required=False,
        label="Active To",
        help_text="Zostaw puste aby nie zmieniać (format: YYYY-MM-DD)",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        active_from = cleaned_data.get("active_from")
        active_to = cleaned_data.get("active_to")

        if active_from and active_to and active_to < active_from:
            raise forms.ValidationError("Data zakończenia nie może być wcześniejsza niż data rozpoczęcia.")

        return cleaned_data


class ActiveStatusFilter(SimpleListFilter):
    title = "Status aktywności"
    parameter_name = "active_status"

    def lookups(self, request, model_admin):
        return (
            ("active", "Aktywne teraz"),
            ("future", "Przyszłe"),
            ("expired", "Wygasłe"),
            ("no_dates", "Bez ograniczeń dat"),
        )

    def queryset(self, request, queryset):
        today = timezone.now().date()
        if self.value() == "active":
            return queryset.filter(
                (Q(active_from__lte=today) | Q(active_from__isnull=True))
                & (Q(active_to__gte=today) | Q(active_to__isnull=True))
            )
        if self.value() == "future":
            return queryset.filter(active_from__gt=today)
        if self.value() == "expired":
            return queryset.filter(active_to__lt=today)
        if self.value() == "no_dates":
            return queryset.filter(active_from__isnull=True, active_to__isnull=True)


class UsageStatusFilter(SimpleListFilter):
    title = "Status wykorzystania"
    parameter_name = "usage_status"

    def lookups(self, request, model_admin):
        return (
            ("unused", "Niewykorzystane (0%)"),
            ("partial", "Częściowo wykorzystane"),
            ("full", "Całkowicie wykorzystane"),
            ("over", "Przekroczone (błąd)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "unused":
            return queryset.filter(current_used=0)
        if self.value() == "partial":
            return queryset.filter(current_used__gt=0, current_used__lt=F("max_used"))
        if self.value() == "full":
            return queryset.filter(current_used=F("max_used"))
        if self.value() == "over":
            return queryset.filter(current_used__gt=F("max_used"))


class ExpiryFilter(SimpleListFilter):
    title = "Wygasa wkrótce"
    parameter_name = "expiry"

    def lookups(self, request, model_admin):
        return (("today", "Dzisiaj"), ("week", "W ciągu 7 dni"), ("month", "W ciągu 30 dni"))

    def queryset(self, request, queryset):
        from datetime import timedelta

        today = timezone.now().date()
        if self.value() == "today":
            return queryset.filter(active_to=today)
        if self.value() == "week":
            return queryset.filter(active_to__gte=today, active_to__lte=today + timedelta(days=7))
        if self.value() == "month":
            return queryset.filter(active_to__gte=today, active_to__lte=today + timedelta(days=30))


class RuleTypeFilter(SimpleListFilter):
    title = "Typ reguły"
    parameter_name = "rule_type"

    def lookups(self, request, model_admin):

        return (
            ("gratis", "Gratis"),
            ("percent", "Procent"),
            ("price", "Kwota"),
            ("free_shipping", "Darmowa dostawa"),
            ("free_order", "Darmowe zamówienie"),
        )

    def queryset(self, request, queryset):
        from django_checkout.models.discount_rule_code import ModifiersForDiscountRule

        if self.value() == "gratis":
            return queryset.filter(
                rule__modifier__in=[
                    ModifiersForDiscountRule.GRATIS_STEPPED,
                    ModifiersForDiscountRule.GRATIS_BY_SKU_IN_CART,
                    ModifiersForDiscountRule.CHEAPEST_GRATIS,
                    ModifiersForDiscountRule.MOST_EXPENSIVE_GRATIS,
                ]
            )
        if self.value() == "percent":
            return queryset.filter(
                rule__modifier__in=[
                    ModifiersForDiscountRule.PERCENT_DISCOUNT,
                    ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT,
                    ModifiersForDiscountRule.STEP_QTY_PERCENT_DISCOUNT_WHOLE_CART,
                    ModifiersForDiscountRule.STEP_PRICE_PERCENT_DISCOUNT,
                ]
            )
        if self.value() == "price":
            return queryset.filter(
                rule__modifier__in=[
                    ModifiersForDiscountRule.PRICE_DISCOUNT,
                    ModifiersForDiscountRule.STEP_QTY_PRICE_DISCOUNT_WHOLE_CART,
                    ModifiersForDiscountRule.STEP_QTY_FIXED_PRICE_PER_CURRENCY,
                ]
            )
        if self.value() == "free_shipping":
            return queryset.filter(rule__free_shipping=True)
        if self.value() == "free_order":
            return queryset.filter(rule__free_order=True)


@admin.register(DiscountCode)
class DiscountCodeAdmin(admin.ModelAdmin):
    list_display = [
        "rule_name",
        "code",
        "rule",
        "max_used",
        "max_uses_per_user",
        "current_used",
        "usage_percentage",
        "active_from",
        "active_to",
        "is_active_now",
        "max_products_qty",
    ]
    model = DiscountCode
    list_filter = [
        "rule",
        ActiveStatusFilter,
        UsageStatusFilter,
        ExpiryFilter,
        RuleTypeFilter,
        "active_from",
        "active_to",
    ]
    search_fields = ["code", "rule__name"]
    autocomplete_fields = ["rule"]
    actions = ["mass_edit_selected"]

    def rule_name(self, obj):
        return obj.rule.name

    def usage_percentage(self, obj):
        """Display usage as percentage with color coding"""
        if obj.max_used == 0:
            return "N/A"
        percentage = (obj.current_used / obj.max_used) * 100
        if percentage >= 100:
            color = "red"
        elif percentage >= 80:
            color = "orange"
        elif percentage >= 50:
            color = "#d4a600"
        else:
            color = "green"
        return mark_safe(f'<span style="color: {color}; font-weight: bold;">{percentage:.0f}%</span>')

    usage_percentage.short_description = "Wykorzystanie"

    def is_active_now(self, obj):
        """Display if code is currently active"""
        today = timezone.now().date()
        is_active = (obj.active_from is None or obj.active_from <= today) and (
            obj.active_to is None or obj.active_to >= today
        )
        if is_active:
            return mark_safe('<span style="color: green;">Aktywny</span>')
        elif obj.active_from and obj.active_from > today:
            return mark_safe('<span style="color: blue;">Przyszły</span>')
        else:
            return mark_safe('<span style="color: red;">Wygasły</span>')

    is_active_now.short_description = "Status"

    def mass_edit_selected(self, request, queryset):
        """Mass edit selected discount codes"""
        from django.core.exceptions import PermissionDenied
        from django.template.response import TemplateResponse

        if not self.has_change_permission(request):
            raise PermissionDenied("Nie masz uprawnień do edycji kodów rabatowych.")

        logger.info(
            f"Mass edit action called. POST keys: {list(request.POST.keys())}, Queryset count: {queryset.count()}"
        )

        if "apply" in request.POST:
            form = MassEditDiscountCodeForm(request.POST)
            if form.is_valid():
                updated_count = 0
                updated_fields = []

                if not any(
                    [
                        form.cleaned_data.get("max_used") is not None,
                        form.cleaned_data.get("max_uses_per_user") is not None,
                        form.cleaned_data.get("max_products_qty") is not None,
                        form.cleaned_data.get("active_from") is not None,
                        form.cleaned_data.get("active_to") is not None,
                    ]
                ):
                    self.message_user(
                        request,
                        "Nie wybrano żadnych pól do edycji. Wypełnij przynajmniej jedno pole.",
                        messages.WARNING,
                    )
                    context = {
                        "form": form,
                        "discount_codes": queryset,
                        "action": "mass_edit_selected",
                        "opts": self.model._meta,
                        "title": f"Masowa edycja {queryset.count()} kodów rabatowych",
                    }
                    return TemplateResponse(request, "admin/discount_code_mass_edit.html", context)

                for discount_code in queryset:
                    changed = False

                    if form.cleaned_data.get("max_used") is not None:
                        discount_code.max_used = form.cleaned_data["max_used"]
                        changed = True
                        if "max_used" not in updated_fields:
                            updated_fields.append("max_used")

                    if form.cleaned_data.get("max_uses_per_user") is not None:
                        discount_code.max_uses_per_user = form.cleaned_data["max_uses_per_user"]
                        changed = True
                        if "max_uses_per_user" not in updated_fields:
                            updated_fields.append("max_uses_per_user")

                    if form.cleaned_data.get("max_products_qty") is not None:
                        discount_code.max_products_qty = form.cleaned_data["max_products_qty"]
                        changed = True
                        if "max_products_qty" not in updated_fields:
                            updated_fields.append("max_products_qty")

                    if form.cleaned_data.get("active_from") is not None:
                        discount_code.active_from = form.cleaned_data["active_from"]
                        changed = True
                        if "active_from" not in updated_fields:
                            updated_fields.append("active_from")

                    if form.cleaned_data.get("active_to") is not None:
                        discount_code.active_to = form.cleaned_data["active_to"]
                        changed = True
                        if "active_to" not in updated_fields:
                            updated_fields.append("active_to")

                    if changed:
                        discount_code.save()
                        updated_count += 1

                logger.info(
                    f"Mass edit by user {request.user.username}: "
                    f"Updated {updated_count} discount codes. Fields: {', '.join(updated_fields)}"
                )

                self.message_user(
                    request,
                    f"Pomyślnie zaktualizowano {updated_count} kodów rabatowych. "
                    f"Zmienione pola: {', '.join(updated_fields)}",
                    messages.SUCCESS,
                )
                from django.urls import reverse

                return redirect(reverse("admin:django_checkout_discountcode_changelist"))
        else:
            form = MassEditDiscountCodeForm()

        context = {
            "form": form,
            "discount_codes": queryset,
            "action": "mass_edit_selected",
            "opts": self.model._meta,
            "title": f"Masowa edycja {queryset.count()} kodów rabatowych",
        }

        return TemplateResponse(request, "admin/discount_code_mass_edit.html", context)

    mass_edit_selected.short_description = "Masowa edycja zaznaczonych kodów"


@admin.register(UsedCoupon)
class UsedCouponAdmin(admin.ModelAdmin):
    model = UsedCoupon
    list_display = [
        "discount_code",
        "channel",
        "order_link",
        "customer",
        "billing_email",
        "shipping_email",
        "order_status_display",
        "order_total",
        "created_at",
    ]
    list_filter = ["channel", "order__order_status", "created_at"]
    search_fields = ["discount_code", "billing_email", "shipping_email", "order__order_id"]
    readonly_fields = [
        "channel",
        "order",
        "customer",
        "billing_email",
        "shipping_email",
        "discount_code",
        "created_at",
        "modified_at",
    ]
    autocomplete_fields = ["order", "customer", "channel"]
    actions = ["export_to_csv"]

    def order_link(self, obj):
        """Display order with link to order admin"""
        if obj.order:
            from django.urls import reverse

            url = reverse("admin:django_checkout_order_change", args=[obj.order.pk])
            return mark_safe(f'<a href="{url}">{obj.order.order_id}</a>')
        return "-"

    order_link.short_description = "Order"
    order_link.admin_order_field = "order"

    def order_status_display(self, obj):
        return obj.order.order_status if obj.order else "-"

    order_status_display.short_description = "Order Status"
    order_status_display.admin_order_field = "order__order_status"

    def order_total(self, obj):
        """Display order total price"""
        if obj.order and obj.order.order_body:
            try:
                total = obj.order.order_body.get("cart", {}).get("total_price", "-")
                currency = obj.order.order_body.get("cart", {}).get("currency_code", "")
                if total != "-":
                    return f"{total} {currency}"
            except (AttributeError, KeyError):
                pass
        return "-"

    order_total.short_description = "Order Total"

    def export_to_csv(self, request, queryset):
        """Export selected UsedCoupons to CSV"""
        import csv

        from django.core.exceptions import PermissionDenied
        from django.utils import timezone

        if not self.has_view_permission(request):
            raise PermissionDenied("Nie masz uprawnień do przeglądania użytych kuponów.")

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="used_coupons_{timestamp}.csv"'

        def clean_string(value):
            """Remove BOM and other zero-width characters"""
            if isinstance(value, str):
                cleaned = value.replace("\ufeff", "").replace("\u200b", "").replace("\ufbff", "")
                if len(cleaned) > 1 and cleaned[0] in ["=", "+", "-", "@", "\t", "\r"]:
                    cleaned = "'" + cleaned

                return cleaned
            return value

        base_headers = [
            "Discount Code",
            "Channel",
            "Order ID",
            "Order Status",
            "Order Total",
            "Currency",
            "Customer ID",
            "Customer Email",
            "Billing Email",
            "Shipping Email",
            "Created At",
            "Order Created At",
            "Products",
            "Discount Amount",
        ]

        rows = []
        extra_headers = []

        for coupon in queryset.select_related("order", "customer", "channel"):
            order_total = "-"
            order_currency = "-"
            order_created = "-"
            products = "-"
            discount_amount = "-"

            if coupon.order:
                if coupon.order.order_body:
                    cart = coupon.order.order_body.get("cart", {})
                    try:
                        order_total = cart.get("total_price", "-")
                        order_currency = coupon.order.order_body.get("currency_code", "-")
                    except (AttributeError, KeyError):
                        pass

                    items = cart.get("items", [])
                    if items:
                        products = " | ".join(f"{item.get('sku', '?')} x{item.get('quantity', '?')}" for item in items)

                    discount_amount = cart.get("discount_amount", "-")

                if coupon.order.created:
                    order_created = coupon.order.created.strftime("%Y-%m-%d %H:%M:%S")

            extra = {}
            responses = order_additional_info.send(sender=Order, order=coupon.order)
            for _receiver, result in responses:
                if isinstance(result, dict):
                    extra.update(result)

            for key in extra:
                if key not in extra_headers:
                    extra_headers.append(key)

            rows.append(
                (
                    [
                        clean_string(coupon.discount_code),
                        clean_string(coupon.channel.idx if coupon.channel else "-"),
                        clean_string(str(coupon.order.order_id) if coupon.order else "-"),
                        clean_string(coupon.order.order_status if coupon.order else "-"),
                        clean_string(str(order_total)),
                        clean_string(order_currency),
                        clean_string(str(coupon.customer.id) if coupon.customer else "-"),
                        clean_string(coupon.customer.user.email if coupon.customer and coupon.customer.user else "-"),
                        clean_string(coupon.billing_email),
                        clean_string(coupon.shipping_email),
                        coupon.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                        order_created,
                        clean_string(products),
                        clean_string(str(discount_amount)),
                    ],
                    extra,
                )
            )

        writer = csv.writer(response, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(base_headers + extra_headers)

        for base_row, extra in rows:
            extra_values = [clean_string(str(extra.get(key, "-"))) for key in extra_headers]
            writer.writerow(base_row + extra_values)

        self.message_user(request, f"Wyeksportowano {queryset.count()} użyć kuponów do CSV.", messages.SUCCESS)

        return response

    export_to_csv.short_description = "Eksportuj zaznaczone do CSV"

    def has_add_permission(self, request, obj=None):
        # UsedCoupon są tworzone automatycznie, nie pozwalamy dodawać ręcznie
        return False


class DiscountModeOdActionsInline(TabularInline):
    model = DiscountModeOfAction
    fields = [
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
        "rule",
        "product_price_from",
        "product_price_to",
        "cart_price_from",
        "cart_price_to",
        "qty_from",
        "qty_to",
        "cart_qty_from",
        "cart_qty_to",
        "is_inclusion_or_exclusion",
        "take_common_part",
    ]
    autocomplete_fields = [
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
    ]
    extra = 0


class DiscountCustomerModeOfActionInline(TabularInline):
    model = DiscountCustomerModeOfAction
    fields = ["customers", "groups", "rule", "take_common_part", "is_inclusion_or_exclusion"]
    autocomplete_fields = ["customers", "groups"]
    extra = 0


class ThresholdProductFilterInline(TabularInline):
    model = ThresholdProductFilter
    fields = [
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
        "rule",
        "product_price_from",
        "product_price_to",
        "cart_price_from",
        "cart_price_to",
        "qty_from",
        "qty_to",
        "cart_qty_from",
        "cart_qty_to",
        "take_common_part",
        "is_inclusion_or_exclusion",
    ]
    autocomplete_fields = [
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
    ]
    extra = 0
    verbose_name = "Threshold Product Filter (for gratis threshold calculation)"
    verbose_name_plural = "Threshold Product Filters (products counted towards gratis threshold)"


class DiscountCodeImportForm(forms.Form):
    csv_file = forms.FileField()
    overwrite_existing = forms.BooleanField(required=False, initial=False, label="Overwrite existing codes")
    delete_old = forms.BooleanField(required=False, initial=False, label="Delete old codes not in CSV")


class CreatedAtDateRangeFilter(SimpleListFilter):
    """Custom filter dla zakresu dat utworzenia."""

    title = "data utworzenia (zakres)"
    parameter_name = "created_range"

    def lookups(self, request, model_admin):
        return [
            ("today", "Dzisiaj"),
            ("week", "Ostatni tydzień"),
            ("month", "Ostatni miesiąc"),
            ("3months", "Ostatnie 3 miesiące"),
            ("year", "Ostatni rok"),
        ]

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == "today":
            return queryset.filter(created_at__date=now.date())
        elif self.value() == "week":
            return queryset.filter(created_at__gte=now - timezone.timedelta(days=7))
        elif self.value() == "month":
            return queryset.filter(created_at__gte=now - timezone.timedelta(days=30))
        elif self.value() == "3months":
            return queryset.filter(created_at__gte=now - timezone.timedelta(days=90))
        elif self.value() == "year":
            return queryset.filter(created_at__gte=now - timezone.timedelta(days=365))
        return queryset


@admin.register(DiscountRuleCode)
class DiscountRuleCodeAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "modifier",
        "display_extra_value",
        "display_min_order_amount",
        "display_codes_count",
        "display_active_codes",
        "display_total_usage",
        "display_channels",
        "display_currencies",
        "target",
        "priority",
        "is_active",
        "automatic_applications",
        "combine_with_other_rules",
        "free_shipping",
        "free_order",
        "is_omnibus",
        "created_at",
    ]
    model = DiscountRuleCode
    inlines = [
        DiscountCodeInline,
        DiscountModeOdActionsInline,
        ThresholdProductFilterInline,
        DiscountCustomerModeOfActionInline,
    ]
    fields = [
        "channels",
        "name",
        "is_active",
        "currencies",
        "free_shipping",
        "free_order",
        "free_shipping_methods",
        "target",
        "extra_value",
        "extension",
        "modifier",
        "is_omnibus",
        "min_order_amount",
        "priority",
        "combine_with_other_rules",
        "automatic_applications",
        "show_when_invalid",
        "created_at",
    ]
    actions = ["export_as_csv"]
    list_filter = [
        "channels",
        "modifier",
        "target",
        "is_active",
        "currencies",
        "automatic_applications",
        "combine_with_other_rules",
        "free_shipping",
        "free_order",
        "is_omnibus",
        "priority",
        CreatedAtDateRangeFilter,
        "created_at",
    ]
    search_fields = ["name", "modifier"]
    change_form_template = "admin/discountrulecode/change_form.html"
    change_list_template = "admin/discountrulecode/discountrulecode_change_list.html"
    autocomplete_fields = ["channels", "currencies"]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path("discountrulecode/csv_import/", self.admin_site.admin_view(self.csv_import), name="csv_import")
        ]
        return custom_urls + urls

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.import_service = DiscountCodeImportService(logger)

    def csv_import(self, request):
        if request.method == "POST" and "file" in request.FILES:
            try:
                csv_file = request.FILES["file"]
                import_discount_rules_from_csv(file=csv_file)
                self.message_user(request, "CSV file processed successfully")
            except Exception as e:
                self.message_user(request, f"Error: {e}")

        return redirect("admin:django_checkout_discountrulecode_changelist")

    def display_extra_value(self, obj):
        """Wyświetla skróconą wersję extra_value z informacją o formacie."""
        if not obj.extra_value:
            return "-"

        if isinstance(obj.extra_value, (int, float)):
            return f"{obj.extra_value} (wszystkie waluty)"

        if isinstance(obj.extra_value, dict):
            if not obj.extra_value:
                return "{} (pusty)"

            first_key = next(iter(obj.extra_value.keys()))
            first_value = obj.extra_value[first_key]

            is_currency_format = (
                isinstance(first_key, str) and len(first_key) == 3 and first_key.isupper() and first_key.isalpha()
            ) or isinstance(first_value, dict)

            if is_currency_format:
                currencies = list(obj.extra_value.keys())
                if len(currencies) <= 3:
                    return f"Per waluta: {', '.join(currencies)}"
                else:
                    return f"Per waluta: {', '.join(currencies[:3])}... (+{len(currencies) - 3})"
            else:
                keys = list(obj.extra_value.keys())
                if len(keys) <= 3:
                    return f"Progi: {', '.join(str(k) for k in keys)} (wszystkie waluty)"
                else:
                    return f"Progi: {', '.join(str(k) for k in keys[:3])}... (+{len(keys) - 3}) (wszystkie waluty)"

        if isinstance(obj.extra_value, list):
            if len(obj.extra_value) <= 3:
                return f"SKU: {', '.join(obj.extra_value)}"
            else:
                return f"SKU: {', '.join(obj.extra_value[:3])}... (+{len(obj.extra_value) - 3})"

        return str(obj.extra_value)[:50]

    display_extra_value.short_description = "Extra Value (Format)"

    def display_channels(self, obj):
        """Wyświetla listę kanałów."""
        channels = obj.channels.all()
        if not channels:
            return "-"

        channel_names = [ch.idx for ch in channels[:3]]
        if len(channels) > 3:
            return f"{', '.join(channel_names)}... (+{len(channels) - 3})"
        return ", ".join(channel_names)

    display_channels.short_description = "Kanały"

    def display_currencies(self, obj):
        """Wyświetla listę kanałów."""
        currencies = obj.currencies.all()
        if not currencies:
            return "-All-"

        currencies_names = [c.iso3 for c in currencies[:4]]
        if len(currencies) > 4:
            return f"{', '.join(currencies_names)}... (+{len(currencies) - 4})"
        return ", ".join(currencies_names)

    display_currencies.short_description = "Waluty"

    def display_min_order_amount(self, obj):
        """Wyświetla minimalną wartość zamówienia."""
        if obj.min_order_amount and obj.min_order_amount > 0:
            return f"{obj.min_order_amount}"
        return "-"

    display_min_order_amount.short_description = "Min. wartość zamówienia"

    def display_codes_count(self, obj):
        """Wyświetla liczbę kodów rabatowych przypisanych do reguły."""
        return obj.codes.count()

    display_codes_count.short_description = "Liczba kodów"
    display_codes_count.admin_order_field = "codes_count"

    def display_active_codes(self, obj):
        """Wyświetla liczbę aktywnych (niewyczerpanych) kodów."""
        active_count = obj.codes.filter(current_used__lt=F("max_used")).count()
        total_count = obj.codes.count()
        if total_count == 0:
            return "-"
        return f"{active_count}/{total_count}"

    display_active_codes.short_description = "Aktywne kody"

    def display_total_usage(self, obj):
        """Wyświetla sumę użyć wszystkich kodów."""
        total_usage = obj.codes.aggregate(total=Sum("current_used"))["total"] or 0
        max_usage = obj.codes.aggregate(total=Sum("max_used"))["total"] or 0
        if max_usage == 0:
            return "-"
        return f"{total_usage}/{max_usage}"

    display_total_usage.short_description = "Użycia (suma)"

    def get_queryset(self, request):
        """Optymalizacja queryset z annotacjami."""
        queryset = super().get_queryset(request)
        queryset = queryset.annotate(codes_count=Count("codes")).prefetch_related("codes", "channels")
        return queryset

    def export_as_csv(self, request, objects):
        utc_tz = pytz.timezone("UTC")
        now = timezone.now().replace(tzinfo=utc_tz)
        import_started_at = now.astimezone(pytz.timezone("Europe/Warsaw"))
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f"attachment; filename=discount_rules-{import_started_at.strftime('%Y%m%d-%H%M%S')}.csv"
        )
        export_discount_rules_to_csv(objects, response)
        return response

    def render_change_form(self, request, context, *args, **kwargs):
        if request.method == "POST" and "csv_file" in request.FILES:
            form = DiscountCodeImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = form.cleaned_data["csv_file"]
                overwrite_existing = form.cleaned_data["overwrite_existing"]
                delete_old = form.cleaned_data["delete_old"]
                discount_rule = context["original"]
                not_imported, errors, deleted_codes_list, code_already_exists = (
                    self.import_service.import_discount_codes(csv_file, discount_rule, overwrite_existing, delete_old)
                )
                if code_already_exists:
                    self.message_user(
                        request, f"Codes already exists in other Discount Rules: {', '.join(code_already_exists)}"
                    )
                if deleted_codes_list:
                    self.message_user(request, f"Deleted codes: {deleted_codes_list}")
                if errors:
                    self.message_user(
                        request,
                        f"Error during import: {', '.join(errors)}. Wrong data format or undefined error",
                        level=messages.ERROR,
                    )
                elif not_imported:
                    if overwrite_existing:
                        message = f"Some codes were overwritten: {', '.join(not_imported)}"
                    else:
                        message = f"Some codes were not imported: {', '.join(not_imported)}, already exists. If you want to overwrite them, check the 'Overwrite existing codes' checkbox"
                    self.message_user(request, message, level=messages.WARNING)
                else:
                    self.message_user(request, "All codes imported successfully", level=messages.SUCCESS)
                return redirect("admin:django_checkout_discountrulecode_change", context["object_id"])
        else:
            form = DiscountCodeImportForm()

        context["import_discount_codes_form"] = form
        return super().render_change_form(request, context, *args, **kwargs)


class ShippingOptionInline(admin.TabularInline):
    model = ShippingOption
    fields = [
        "country",
        "country_code",
        "currency",
        "tax_rate",
        "price_brutto",
        "new_price",
        "free_delivery_above_brutto",
        "cash_on_delivery_available",
        "cash_on_delivery_fee",
    ]
    ordering = ["country"]


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    model = ShippingMethod
    list_display = ["code", "method_type", "channel", "name_t9n", "price_type", "max_weight", "position"]
    list_filter = ["channel", "method_type"]
    inlines = [ShippingOptionInline]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    model = PaymentMethod
    list_display = ["code", "provider", "channel", "name_t9n", "fee_type", "fee_value", "position", "customer_group"]
    list_filter = ("channel",)


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    model = PaymentIntent
    list_display = ["order", "method", "payment_status", "code", "amount", "currency", "external_order_id"]
    list_filter = ["order__channel", "method", "payment_status"]
    search_fields = ["order__order_id", "external_order_id"]
    fields = [
        "order",
        "method",
        "code",
        "amount",
        "currency",
        "payment_status",
        "external_order_id",
        "redirect_url",
        "provider_notify",
        "provider_request",
    ]
    # amount/currency are placement-time snapshots set by Order.create — display only.
    readonly_fields = [
        "order",
        "method",
        "code",
        "amount",
        "currency",
        "external_order_id",
        "redirect_url",
        "provider_notify",
    ]


@admin.register(PaymentRedirect)
class PaymentRedirectAdmin(admin.ModelAdmin):
    model = PaymentRedirect
    list_display = ["token", "order", "created_at", "expires_at", "consumed_at"]
    list_filter = ["order__channel"]
    search_fields = ["token", "order__order_id"]
    fields = ["token", "order", "target_url", "created_at", "modified_at", "expires_at", "consumed_at"]
    readonly_fields = ["token", "order", "target_url", "created_at", "modified_at", "expires_at", "consumed_at"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    model = Stock
    fields = ["product", "saleable_quantity_limit", "quantity", "saleable_quantity_limit_per_order", "supplier"]
    search_fields = ["product__sku"]
    autocomplete_fields = ("product",)
    list_display = ["sku", "quantity", "saleable_quantity_limit_per_order", "supplier"]
    list_filter = ["product__channel", "supplier", "supplier__is_global"]

    def sku(self, obj):
        return obj.product.sku


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    model = StockReservation
    search_fields = ["stock__product__sku", "order__order_id"]
    list_display = ["stock", "sku", "reserved_quantity", "order"]
    list_filter = ["stock__product__channel"]
    autocomplete_fields = ("stock",)

    def sku(self, obj):
        return obj.stock.product.sku


@admin.register(LimitedProducts)
class LimitedProductsAdmin(admin.ModelAdmin):
    model = LimitedProducts
    search_fields = ["idx", "association_type", "shipping_method__code", "payment_method__code"]
    list_display = [
        "idx",
        "value",
        "association_type",
        "shipping_method",
        "payment_method",
        "authentication_state",
        "is_active",
    ]
    list_filter = ["shipping_method__channel", "association_type", "payment_method__channel", "authentication_state"]


@admin.register(LinkedProducts)
class LinkedProductsAdmin(admin.ModelAdmin):
    model = LinkedProducts
    list_display = [
        "idx",
        "value",
        "association_type",
        "shipping_method",
        "payment_method",
        "authentication_state",
        "is_active",
    ]
    search_fields = ["idx", "association_type", "shipping_method__code", "payment_method__code"]
    list_filter = ["shipping_method__channel", "association_type", "payment_method__channel", "authentication_state"]


@admin.register(OrderStatusLabel)
class OrderStatusLabelAdmin(admin.ModelAdmin):
    model = OrderStatusLabel
    list_display = ["channel", "status", "name"]
    list_filter = ("channel",)


@admin.register(OrderAttachment)
class OrderAttachmentAdmin(admin.ModelAdmin):
    model = OrderAttachment
    list_display = ["name", "attachment", "order"]
    search_fields = ["name", "order"]
    list_filter = ("order__channel",)


@admin.register(ProductRepresentation)
class ProductRepresentationAdmin(admin.ModelAdmin):
    model = ProductRepresentation
    fields = ["sku", "channel", "origin_sku"]
    list_display = ["sku", "channel", "origin_sku"]
    search_fields = ["sku"]
    list_filter = ["channel"]


@admin.register(ShippingOption)
class ShippingOptionAdmin(admin.ModelAdmin):
    model = ShippingOption
    list_display = [
        "method",
        "channel",
        "country",
        "currency",
        "tax_rate",
        "price_brutto",
        "new_price",
        "free_delivery_above_brutto",
        "cash_on_delivery_available",
        "cash_on_delivery_fee",
    ]
    list_filter = ["method__channel", "method__code"]


class ShippingPriceByWeightInline(admin.TabularInline):
    model = ShippingPriceByWeight
    list_display = ["weight", "price"]
    list_filter = [
        "shipping_price_matrix__shipping_option__method__channel",
        "shipping_price_matrix__shipping_option__method__code",
    ]
    extra = 0


@admin.register(ShippingPriceMatrix)
class ShippingPriceMatrixAdmin(admin.ModelAdmin):
    model = ShippingPriceMatrix
    list_display = ["shipping_option", "filter_type", "idx", "value"]
    list_filter = ["shipping_option__method__channel", "shipping_option__method__code", "filter_type"]
    inlines = [ShippingPriceByWeightInline]


@admin.register(ShippingIntent)
class ShippingIntentAdmin(admin.ModelAdmin):
    model = ShippingIntent
    list_display = ["order", "method", "code", "tracking_number", "tracking_link"]
    list_filter = ["order__channel", "method"]
    search_fields = ["order__order_id"]
    fields = ["order", "method", "code", "tracking_number", "tracking_link"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    model = Invoice
    list_display = ["invoice_id", "order", "invoice_number", "created"]
    list_filter = ["order__channel"]
    search_fields = ["order__order_id", "invoice_id"]
    fields = ["order", "invoice_number", "invoice_base64"]
    readonly_fields = ["invoice_id", "created"]
    autocomplete_fields = ("order",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    model = Supplier
    list_display = ["code", "name", "channel", "is_global"]
    list_filter = ["channel", "is_global"]
    search_fields = ["code", "name"]


@admin.register(SupplierCustomer)
class SupplierCustomerAdmin(admin.ModelAdmin):
    model = SupplierCustomer
    list_display = ["supplier", "customer"]
    list_filter = ["supplier__channel", "supplier"]
    search_fields = ["customer__user__email"]
    autocomplete_fields = ["customer"]


class SaleOfferPriceInline(admin.TabularInline):
    model = SaleOfferPrice
    list_display = ["currency", "country_code", "price_from", "price_to", "actual_price_admin"]
    readonly_fields = ["actual_price"]
    extra = 1

    def actual_price_admin(self, obj):
        return obj.actual_price


@admin.action(description="Toggle is_active for selected SaleOffers")
def toggle_is_active(modeladmin, request, queryset):
    for sale_offer in queryset:
        sale_offer.is_active = not sale_offer.is_active
        sale_offer.save()


@admin.register(SaleOffer)
class SaleOfferAdmin(admin.ModelAdmin):
    model = SaleOffer
    list_display = ["product", "is_active"]
    inlines = [SaleOfferPriceInline]
    search_fields = ["product__sku"]
    autocomplete_fields = ["product"]
    actions = [toggle_is_active]


@admin.register(DiscountModeOfAction)
class DiscountModeOfActionAdmin(admin.ModelAdmin):
    list_display = [
        "rule_name",
        "product_price_from",
        "product_price_to",
        "cart_price_from",
        "cart_price_to",
        "qty_from",
        "qty_to",
        "cart_qty_from",
        "cart_qty_to",
        "is_inclusion_or_exclusion",
        "take_common_part",
    ]
    model = DiscountModeOfAction
    list_filter = ["rule"]
    search_fields = ["rule", "rule__name"]
    autocomplete_fields = [
        "rule",
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
    ]

    def rule_name(self, obj):
        return obj.rule.name


@admin.register(CustomsThresholdConfig)
class CustomsThresholdConfigAdmin(admin.ModelAdmin):
    model = CustomsThresholdConfig
    list_display = [
        "country",
        "scheme_code",
        "threshold_value",
        "threshold_currency",
        "channel_to_threshold_rate",
        "scheme_number",
        "apply_threshold_to_shipping",
        "is_active",
        "updated",
    ]
    list_filter = ["is_active", "scheme_code", "apply_threshold_to_shipping"]
    search_fields = ["country__iso2", "scheme_code", "scheme_number"]
    autocomplete_fields = ["country", "threshold_currency"]
    readonly_fields = ["updated"]


@admin.register(ThresholdProductFilter)
class ThresholdProductFilterAdmin(admin.ModelAdmin):
    list_display = [
        "rule_name",
        "product_price_from",
        "product_price_to",
        "cart_price_from",
        "cart_price_to",
        "qty_from",
        "qty_to",
        "cart_qty_from",
        "cart_qty_to",
        "is_inclusion_or_exclusion",
        "take_common_part",
    ]
    model = ThresholdProductFilter
    list_filter = ["rule"]
    search_fields = ["rule", "rule__name"]
    autocomplete_fields = [
        "rule",
        "products",
        "categories",
        "attributes",
        "features_qty_greater_than_attr_value",
        "features_qty_is_multiple_of_attr_value",
    ]

    def rule_name(self, obj):
        return obj.rule.name


@admin.register(DiscountCustomerModeOfAction)
class DiscountCustomerModeOfActionAdmin(admin.ModelAdmin):
    model = DiscountCustomerModeOfAction
    list_display = ["rule_name", "rule", "take_common_part", "is_inclusion_or_exclusion"]
    search_fields = ["rule__name"]
    autocomplete_fields = ["rule", "customers", "groups"]

    def rule_name(self, obj):
        return obj.rule.name
