# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os

from django.conf import settings

from django_checkout.enums.enums import OrderStatus

#
# API
#
API_VERSION = "v1"
PUBLIC_BASE_URL = getattr(settings, "API_PUBLIC_BASE_URL", "api").strip("/")
ADMIN_BASE_URL = getattr(settings, "API_ADMIN_BASE_URL", "api-admin").strip("/")

CHECKOUT_SALEABLE_QUANTITY_LIMIT = getattr(settings, "CHECKOUT_SALEABLE_QUANTITY_LIMIT", 0)

# Ilość elementów w bulk_update, zbyt duża ilość może znacznie spowolnic baze danych
CHECKOUT_UPDATE_QUANTITIES_CHUNK_SIZE = int(getattr(settings, "CHECKOUT_UPDATE_QUANTITIES_CHUNK_SIZE", 10000))
MAX_REQUESTED_DELIVERY_DATE = getattr(settings, "MAX_REQUESTED_DELIVERY_DATE", 21)

IS_VAT_OSS_TURN_ON = getattr(
    settings, "IS_VAT_OSS_TURN_ON", False
)  # definiuje czy chcekout powinien być liczony według kraju dostawy

EXPORT_DIR = settings.EXPORT_DIR
ORDER_STATUSES_QUALIFIED_TO_FIRST_ORDER = getattr(
    settings,
    "ORDER_STATUSES_QUALIFIED_TO_FIRST_ORDER",
    [OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED],
)

ORDER_STATUSES_QUALIFIED_TO_DISCOUNT_MAX_PER_USER = getattr(
    settings,
    "ORDER_STATUSES_QUALIFIED_TO_DISCOUNT_MAX_PER_USER",
    [OrderStatus.COMPLETED, OrderStatus.RETURNED, OrderStatus.CANCELED],
)


# Jeżeli GRATIS_MECHANISM == 1 to obniż cenę gratisu o procent GRATIS_PERCENT, jeżeli cena spadnie poniżej GRATIS_PRICE ustaw GRATIS_PRICE
# Jeżeli GRATIS_MECHANISM == 2 to obniżaj cenę gratisu do ceny GRATIS_PRICE, jednakowo dla każdej waluty

GRATIS_MECHANISM = getattr(settings, "GRATIS_MECHANISM", 1)  # percentage, fixed
GRATIS_PRICE = getattr(settings, "GRATIS_PRICE", 1)  # minimalna cena gratisu
GRATIS_PERCENT_DISCOUNT = getattr(settings, "GRATIS_PERCENT_DISCOUNT", 99)  # procentowa wartość obniżki na gratis

# CUSTOMER FILES
PRIVATE_DIR = getattr(settings, "PRIVATE_DIR", None)
if not PRIVATE_DIR:
    raise OSError("Setting PRIVATE_DIR is not set. Default should be '/private'.")
ORDER_DIR = os.path.join(PRIVATE_DIR, "order/")

# This setting determines if price tuner module is being used in checkout
USE_PRICE_TUNER_IN_CHECKOUT = getattr(settings, "USE_PRICE_TUNER_IN_CHECKOUT", False)
CUSTOMER_PRICES_PRICE_TUNER_IN_CHECKOUT = getattr(settings, "CUSTOMER_PRICES_PRICE_TUNER_IN_CHECKOUT", False)

# This settings when = True, will add max available quantity to the checkout when quantity is higher than available
# This settings when = False, in cart mark item line as invalid when quantity is higher than available
ADD_MAX_AVAILABLE_QUANTITY = getattr(settings, "ADD_MAX_AVAILABLE_QUANTITY", True)

SANDBOX_AUTOPAY_GATEWAY_ID = getattr(settings, "SANDBOX_AUTOPAY_GATEWAY_ID", 1500)

# Trun on deduplication for shipping methods method types. Available values: "default", "inpost"
SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES = getattr(
    settings, "SHIPPING_METHOD_METHOD_TYPE_DEDUPLICATION_IDXES", []
)
GLOBAL_STOCK_RESERVATION_CHANNELS = getattr(settings, "GLOBAL_STOCK_RESERVATION_CHANNELS", [])
GLOBAL_STOCK_DIFFERENT_SUPPLIERS = getattr(settings, "GLOBAL_STOCK_DIFFERENT_SUPPLIERS", False)

if len(GLOBAL_STOCK_RESERVATION_CHANNELS) == 1:
    raise OSError("Setting GLOBAL_STOCK_RESERVATION_CHANNELS cannot have only one channel.")

# Autopay

# example: AUTOPAY_HASH_KEY = {"uk": "4e4690a86a119a737601d60a739e80fc9b79ccd8", "global-america": "0e4a6b71608f6a3f1e472a2b91d1ceeb8b0338e2", "global-rest": "4799537541edd9fd5ea033bfa666aba818846573"}

AUTOPAY_HASH_KEY = getattr(settings, "AUTOPAY_HASH_KEY", None)


# This setting determines source of country for checkout
# 0 - from body or json cart or default country channel
# 1 - from header cf/last country customer or body or json cart or default country channel
# 2 - from header cf/last country if not found return user cannot proceed cart

COUNTRY_CHECKOUT_MECHANISM = getattr(settings, "COUNTRY_CHECKOUT_MECHANISM", 0)

if COUNTRY_CHECKOUT_MECHANISM in [1, 2] and IS_VAT_OSS_TURN_ON:
    raise OSError("Setting COUNTRY_CHECKOUT_MECHANISM cannot be 1 or 2 when IS_VAT_OSS_TURN_ON is True.")

# Use Cashe for views (API) Default ON/OFF: OFF, Default TTL: 15 min
USE_CACHED_VIEWS = getattr(settings, "USE_CACHED_VIEWS", False)
CACHE_TTL = getattr(settings, "CACHE_TTL", 60 * 15)

#
PAYNOW_VALIDITY_TIME_ORDER_DAYS = getattr(settings, "PAYNOW_VALIDITY_TIME_ORDER_DAYS", 10)
PAYNOW_VALIDITY_TIME_SECONDS = getattr(settings, "PAYNOW_VALIDITY_TIME_SECONDS", None)
INCLUDE_GRATIS_IN_QUANTITY_DISCOUNT_COUNTER = getattr(settings, "INCLUDE_GRATIS_IN_QUANTITY_DISCOUNT_COUNTER", False)

# Jeżeli True: łączone kody rabatowe o niższym priorytecie aplikują się
# tylko na produkty, które nie otrzymały rabatu od wyższego priorytetu.
# Dotyczy wyłącznie reguł z combine_with_other_rules=True.
# Nie dotyczy reguł GRATIS — te przechodzą oddzielną ścieżką.
RESTRICT_COMBINE_BY_PRIORITY = getattr(settings, "RESTRICT_COMBINE_BY_PRIORITY", False)

# Ustawienie czy używać sygnałów do walidacji itemów
USE_VALIDATE_ITEMS_SIGNAL = getattr(settings, "USE_VALIDATE_ITEMS_SIGNAL", True)
# Voucher validation signal — emitted alongside validate_items in cart validation.
# Default False; flip to True in services that install django-checkout-voucher.
USE_VALIDATE_VOUCHERS_SIGNAL = getattr(settings, "USE_VALIDATE_VOUCHERS_SIGNAL", False)
PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK = getattr(
    settings, "PAYPAL_CAPTURE_PAYMENT_AFTER_APPROVED_ORDER_WEBHOOK", True
)

TURN_ON_VIES_VALIDATION_AND_0_VAT_WHEN_VALID_AND_OUTSIDE_PL = getattr(
    settings, "TURN_ON_VIES_VALIDATION_AND_0_VAT_WHEN_VALID_AND_OUTSIDE_PL", False
)
BLIK_REGEX_VALIDATION = getattr(settings, "BLIK_REGEX_VALIDATION", r"^\d{6}$")

LIMIT_PRODUCT_CACHE_TIME = getattr(settings, "LIMIT_PRODUCT_CACHE_TIME", 1 * 60)
CHECKOUT_USE_ISO_DATETIME_FORMAT = getattr(settings, "CHECKOUT_USE_ISO_DATETIME_FORMAT", False)

CUSTOMS_THRESHOLD_ENABLED = getattr(settings, "CUSTOMS_THRESHOLD_ENABLED", False)
SHOW_INVALID_GRATIS = getattr(settings, "SHOW_INVALID_GRATIS", False)


VALIDATE_ADDRESS_EMAIL = getattr(settings, "VALIDATE_ADDRESS_EMAIL", True)
# If True and all custom attributes are default, use source_product SKU instead of custom SKU
USE_SOURCE_PRODUCT_WHILE_CONFIGURABLE_ALL_ATTS_DEFAULT = getattr(
    settings, "USE_SOURCE_PRODUCT_WHILE_CONFIGURABLE_ALL_ATTS_DEFAULT", False
)

# PAYMENT REDIRECT BRIDGE
# Client URL schemes that skip the bridge (passed to the payment gateway as-is).
PAYMENT_REDIRECT_DIRECT_SCHEMES = getattr(settings, "PAYMENT_REDIRECT_DIRECT_SCHEMES", ["https"])
# URL schemes allowed as a bridge redirect target (e.g. "exp" for Expo, "myapp" for a native app).
# Service deployments extend this list with their own custom schemes.
PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES = getattr(settings, "PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES", ["https"])
# Bridge token TTL (after expiry the endpoint returns 404).
PAYMENT_REDIRECT_BRIDGE_TTL_MINUTES = int(getattr(settings, "PAYMENT_REDIRECT_BRIDGE_TTL_MINUTES", 120))
# Hosts allowed as a bridge target for web schemes (http/https). target_url comes from
# the client, so without this list the bridge would be an open redirector on the shop's
# domain and certificate. Empty = no web target passes (native app schemes have no
# meaningful host and are controlled by scheme alone).
PAYMENT_REDIRECT_ALLOWED_HOSTS = getattr(settings, "PAYMENT_REDIRECT_ALLOWED_HOSTS", [])

# PAY_CODE BRUTE-FORCE LIMIT
# Voucher codes are bearer instruments and every guess targets a different code, so a
# per-voucher counter never trips. Rate-limit per IP on requests CARRYING pay_code —
# regular cart operations are not throttled.
PAY_CODE_RATE_LIMIT = int(getattr(settings, "PAY_CODE_RATE_LIMIT", 30))
PAY_CODE_RATE_WINDOW_SECONDS = int(getattr(settings, "PAY_CODE_RATE_WINDOW_SECONDS", 60))
