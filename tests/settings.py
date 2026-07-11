# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import dj_database_url

SECRET_KEY = "test-secret-key-for-checkout-v2"

# Settings required at django.setup() time by apps in INSTALLED_APPS
# (django_accounts and django_checkout raise if these are unset).
JWT_SECRET = "test-jwt-secret-for-checkout-v2"
PRIVATE_DIR = "/tmp/checkout-test-private"
EXPORT_DIR = "/tmp/checkout-test-export"
MIGRATION_0023_MECHANISM = 1

# Required by django_pim at import time.
MEDIA_URL = "/media/"
STATIC_URL = "/static/"
TMP_DIR = "/tmp/checkout-test-tmp"

# Required by django_checkout.bi at import time.
BI_ENVIRONMENT = "test"
BI_BUSINESS_UNIT = "test"

# Postgres required (JSONField lookups, cross-app FK to pim/accounts/regional squashes).
# CI provides DATABASE_URL, locally point it at any postgres 15+.
DATABASES = {
    "default": dj_database_url.config(default="postgresql://postgres:postgres@localhost:5432/test"),
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "drf_spectacular",
    "django_regional",
    "django_accounts",
    "django_pim",
    "django_pricemanager",
    "django_checkout",
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Checkout API v2",
    "VERSION": "2.0.0",
}

# Checkout-specific settings
API_BASE_URL = "/api/"
