# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.apps import AppConfig


class DjangoCheckoutConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_checkout"
    verbose_name = "Checkout"
    is_volkanos = True
    # Copied 1:1 from entirius-django-access cf538d2 catalogue defaults;
    # the access defaults stay until this module's release.
    access_areas = [
        {"key": "checkout.orders", "label": "Orders", "sensitive": ("pii", "money")},
        {"key": "checkout.discounts", "label": "Discounts and discount codes", "sensitive": ("money",)},
    ]
    access_token_scopes = [
        {
            "key": "checkout.storefront",
            "label": "Storefront carts and orders",
            "publishable": True,
            "routes": (
                "/api/checkout/v2/{channel_idx}/carts/**",
                "/api/checkout/v2/{channel_idx}/orders/**",
                "/api/checkout/v2/{channel_idx}/countries/",
                "/api/checkout/{version}/{channel_idx}/carts/**",
                "/api/checkout/{version}/{channel_idx}/orders/**",
                "/api/checkout/{version}/{channel_idx}/customer/cart/",
            ),
        },
        {
            "key": "checkout.erase",
            "label": "Anonymise a customer's orders (GDPR)",
            "publishable": False,
            "routes": ("/api-admin/checkout/{version}/{channel_idx}/customer/delete",),
        },
    ]
    # Every admin view carries its access_area; no route needs a path rule.
    access_route_rules = []

    def ready(self):
        pass
