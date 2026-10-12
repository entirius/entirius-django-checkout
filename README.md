# entirius-django-checkout

A Django module for carts and orders in Entirius, the open-source modular ecommerce platform. It runs the checkout
of a multi-channel storefront: cart lifecycle, order creation and splitting, payments through pluggable gateways,
shipping and payment method selection, discounts and stock reservation.

[![PyPI](https://img.shields.io/pypi/v/entirius-django-checkout.svg)](https://pypi.org/project/entirius-django-checkout/)
[![Python](https://img.shields.io/pypi/pyversions/entirius-django-checkout.svg)](https://pypi.org/project/entirius-django-checkout/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](https://github.com/entirius/entirius-django-checkout/blob/develop/LICENSE)

## What it does

- Keeps carts and turns them into orders, with order splitting by configurable rules.
- Takes payments through PayU, PayPal, Autopay, Przelewy24, Paynow and Stripe, including their notification
  callbacks.
- Offers shipping and payment methods per sales channel, country and currency.
- Applies discount codes, discount rules and free-product (gratis) rules.
- Reserves stock per product and supplier.
- Exposes two REST API generations side by side: v1 and v2, where each PATCH updates one part of the cart.

## Installation

```shell
uv add entirius-django-checkout
pip install entirius-django-checkout
```

Optional integrations are extras (`qms`: stock sync from warehouses, `vault`: saved payment cards, `returns`:
order returns):

```shell
uv add "entirius-django-checkout[qms,vault,returns]"
```

```python
INSTALLED_APPS = [
    ...
    "django_utils",
    "django_regional",
    "django_accounts",
    "django_pim",
    "django_pricemanager",
    "django_checkout",
]

# The list works first in, first out: the first entry is checked first.
AUTHENTICATION_BACKENDS = [
    "django_accounts.backends.JWTAccessBackend",
    "django.contrib.auth.backends.ModelBackend",  # default Django auth, needed for /admin
]

# urls.py
urlpatterns = [
    path("", include("django_checkout.urls")),
]
```

Required settings: `PRIVATE_DIR`, `EXPORT_DIR`, `BI_ENVIRONMENT` and `BI_BUSINESS_UNIT`; the module refuses to
import without them. Reference with the optional toggles:
https://github.com/entirius/entirius-django-checkout/blob/develop/docs/configuration.md.

The storefront API reads its key from the `X-API-KEY` header. With `django_access` installed the key is an access
token: scope `checkout.storefront` for the storefront key, `checkout.erase` for the admin erase key (which also
needs `--expires-days <days>`):

```shell
python manage.py access_token create --application <name> --create-application --scope checkout.storefront --channel <idx>
```

Without `django_access`, create the keys with `python manage.py generate-api-key <channel_idx>` and
`python manage.py generate-api-admin-key <channel_idx>`.

## Quick start

```python
from django_checkout.models import Channel
from django_checkout.services.cart_service import get_cart

channel = Channel.objects.get(idx="default")
cart = get_cart(channel, cart_id)
```

## Requirements

- Python 3.11 or newer, Django 5.0 or newer.
- PostgreSQL.
- Entirius packages pulled in: `entirius-django-utils`, `entirius-django-regional`, `entirius-django-pim`,
  `entirius-django-accounts`, `entirius-django-pricemanager`, the payment gateway SDKs (`entirius-py-payu-sdk`,
  `entirius-py-paypal-sdk`, `entirius-py-autopay-sdk`, `entirius-py-przelewy24-sdk`, `entirius-py-paynow-sdk`),
  `entirius-py-bievents`, `entirius-py-idx-normalizator`, `entirius-py-process-logger`.

## Documentation

- Module docs: https://github.com/entirius/entirius-django-checkout/tree/develop/docs
- Entirius documentation: https://docs.entirius.com/
- Changelog: https://github.com/entirius/entirius-django-checkout/blob/develop/CHANGELOG.md

## Part of Entirius

This module runs inside the Entirius backend service (Volkanos), next to the catalog, price and account modules.

- Backend service: https://github.com/entirius/entirius-service-volkanos
- Docker harness: https://github.com/entirius/entirius-zeno
- Sibling modules: https://github.com/entirius/entirius-django-pricemanager,
  https://github.com/entirius/entirius-django-accounts, https://github.com/entirius/entirius-django-returns,
  https://github.com/entirius/entirius-django-vault, https://github.com/entirius/entirius-django-qms

## Development

```shell
make install     # sync dependencies (uv)
make check       # lint + format check (ruff)
make test        # test suite (pytest + pytest-django, needs PostgreSQL via DATABASE_URL)
```

Development and agent instructions: https://github.com/entirius/entirius-django-checkout/blob/develop/AGENTS.md

## License

Mozilla Public License 2.0 (MPL-2.0) — see https://github.com/entirius/entirius-django-checkout/blob/develop/LICENSE.
