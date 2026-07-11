# entirius-django-checkout

Checkout engine for Django.

`django_checkout` handles the full checkout flow of a multi-channel storefront: cart lifecycle,
order creation and splitting, payment intents with pluggable providers (PayU, PayPal, Autopay,
Przelewy24, Paynow, Stripe), shipping and payment method selection, discount codes and rules,
sale offers and per-supplier stock reservation. Two API generations coexist: v1 (function-based
views, marshmallow DTOs) and v2 (DRF + Pydantic, sub-resource PATCH pattern).

## Installation

```shell
pip install entirius-django-checkout
```

Add `django_checkout` to `INSTALLED_APPS`. The checkout attaches to `django_accounts`,
`django_pim`, `django_pricemanager` and `django_regional`; optional integrations are exposed
as extras:

```shell
pip install "entirius-django-checkout[qms,vault,vat,pricetuner]"
```

To use the `django_accounts` auth system, add its backend in the host service settings
(the list works FIFO — first entry is checked first):

```python
AUTHENTICATION_BACKENDS = [
    "django_accounts.backends.JWTAccessBackend",
    "django.contrib.auth.backends.ModelBackend",  # default django auth - important for /admin
]
```

## Development

```shell
uv sync --all-extras
make check test
```

Tests require PostgreSQL — point `DATABASE_URL` at any postgres 15+.
See [AGENTS.md](AGENTS.md) for architecture, settings and integration points.
