# AGENTS.md

Checkout engine for Django: carts, orders, payment intents, shipping, discounts and sale offers —
distribution `entirius-django-checkout`, Django app `django_checkout`.

Handles cart lifecycle, order creation, payment provider integration (PayU, PayPal, Autopay,
Przelewy24, Paynow, Stripe), discount codes, shipping/payment method selection, stock reservation
and order splitting.

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |
| `make test-legacy` | test suite without django_access (legacy key path) |

Tests need PostgreSQL: set `DATABASE_URL` (see `docs/testing.md`).

## Conventions

- English only: code, docs, commits, branches, PRs.
- MPL-2.0: every non-trivial source file carries the license header (pre-commit inserts it).
- Toolchain: uv + ruff + hatchling + pytest; all config in `pyproject.toml`; `uv.lock` committed.
- Git flow: `master` (production) + `develop` (integration); changes land via PR; semver tag on `master`.
- Never rename the package / Django app_label / DB table prefix `django_checkout` — it is a schema contract.
- Migrations are part of the public contract — never edit an already released migration.
- Access: areas and token scopes live on the AppConfig (`access_areas`, `access_token_scopes`,
  `access_route_rules`), every admin view carries `access_area`; a new admin route without one fails
  `tests/test_access_ownership.py`.
- Default: do not commit — git is the user's call.

### Commit Message Format

**NEVER add `Co-Authored-By: Claude ...` (or any other Claude/Anthropic attribution) to commit messages.**

This overrides the default Claude Code behavior of appending a `Co-Authored-By` trailer. Commit messages MUST contain only the user's authored content — no robot footer, no "Generated with Claude Code" line, no co-author trailer.

Same rule applies to PR descriptions: no `Generated with [Claude Code]` footer.

## Architecture

```
src/django_checkout/
├── models/                  # Django models (ORM layer)
│   ├── cart.py              # Cart with JSONField cart_body
│   ├── order.py             # Order with JSONField order_body
│   ├── channel.py           # Checkout channel config (discount mode, split rules)
│   ├── payment_method.py    # Payment methods per channel
│   ├── shipping_method.py   # Shipping methods per channel
│   ├── shipping_option.py   # Country/currency-specific shipping prices
│   ├── discount_code.py     # Discount codes (usage limits, dates)
│   ├── discount_rule_code.py # Discount rules (modifier types)
│   ├── stock.py             # Stock per product per supplier
│   └── ...
├── domain/                  # Business logic (v1/v2 shared)
│   ├── cart.py              # process_cart() — main processing pipeline
│   ├── dto/                 # Marshmallow dataclass DTOs
│   │   ├── cart.py          # CheckoutData, CartRequest, CartResponse
│   │   ├── item.py          # ItemData, ValidatedItemData (price fields)
│   │   ├── shipping.py      # ShippingData, ValidatedShippingData
│   │   ├── payment.py       # PaymentData, ValidatedPaymentData
│   │   ├── discount.py      # DiscountData, ValidatedDiscountData
│   │   └── address.py       # AddressData (billing/shipping)
│   └── validators/          # Cart, item, shipping, payment, discount validators
├── views/                   # v1 function-based views (stays untouched)
├── api/                     # v2 DRF views (new)
│   └── v2/
│       ├── views/           # DRF APIViews (cart, order, admin)
│       ├── urls.py          # v2 URL patterns
│       ├── permissions.py   # ChannelAPIKey, AdminJWT
│       ├── mixins.py        # CheckoutChannelMixin
│       └── exceptions.py    # v2 error format
├── schemas/                 # Pydantic models (v2 only)
│   ├── common.py            # MoneyField, TaxRateField
│   ├── requests/            # v2 request schemas
│   └── responses/           # v2 response schemas (price field rename layer)
├── services/                # Service layer
│   ├── cart_service.py      # Cart CRUD + sub-resource patches
│   ├── cart_response_builder.py  # Domain -> v2 response translation
│   ├── error_mapper.py      # v1 messages -> v2 error details
│   ├── customer.py          # Customer anonymization
│   └── stock_sync_service.py # QMS warehouse sync
├── worker/                  # Discount calculation, order splitting
├── enums/                   # Status enums, payment providers
├── management/commands/     # Import/export commands
├── signals/                 # Order/cart event signals
├── migrations/              # single squashed migration (replaces the historical chain)
└── settings.py              # Module config (URL bases, chunk sizes)
```

Two API generations coexist: v1 (function-based views, marshmallow DTOs) and v2 (DRF APIViews, Pydantic schemas,
sub-resource PATCH pattern).

Soft integrations (optional extras, lazy imports with graceful fallback): `qms` (stock sync command reads QMS
sources), `vault` (card-on-file storage for PayU card payments), `returns` (returnable-orders lookup in
`views/order.py`).

| Topic | Where |
|---|---|
| Data model | `docs/data-model.md` |
| API surface (v1, v2), cart interfaces | `docs/api/checkout.md` |
| API keys, errors, discounts, gratis | `docs/api/auth.md`, `docs/api/errors.md`, `docs/api/discounts.md`, `docs/api/gratis.md` |
| Payment providers | `docs/api/payu.md`, `docs/api/paypal.md`, `docs/api/przelewy24.md` |
| Settings reference, soft integrations | `docs/configuration.md` |
| Management commands | `docs/commands/README.md` |
| Price fields of the cart | `docs/django_checkout_prices_info.md` |
| Testing | `docs/testing.md` |

## Settings

Fail-fast (raise at import when unset): `PRIVATE_DIR`, `EXPORT_DIR`; `django_checkout.bi` requires
`BI_ENVIRONMENT` and `BI_BUSINESS_UNIT`. Every other setting has a default in `django_checkout/settings.py`;
toggles and defaults: `docs/configuration.md`.

## Gotchas

- `cart_body` JSONField is the shared contract between v1 and v2. Both read/write the same blob.
- `process_cart()` in `domain/cart.py` is the main processing pipeline — shared by v1 and v2.
- v1 and v2 URLs coexist. v2 patterns MUST be before v1's `<str:version>` in `urls.py`.
- Payment webhooks stay v1 only (external provider contracts).
- `tax_rate` is integer `23` in v1 domain, decimal string `"0.23"` in v2 response.
- Discount modifier types include stepped, gratis, percentage, fixed.
- Order splitting creates multiple orders from one cart based on feature/attribute rules.
- Keys: `utils/api_keys.py` `key_is_valid` is the one check (v1 decorators, v2 permission). With
  `django_access` installed it calls `verify_api_key` (scopes `checkout.storefront` / `checkout.erase`) and never
  reads `APIKey` / `APIAdminKey`; legacy keys work only as imported tokens. Soft dependency — never in `pyproject.toml`.
  Without `django_access`, legacy keys come only from `generate-api-key` / `generate-api-admin-key` (the admin masks
  keys, so a key added there could not be read back). Erase tokens need `--expires-days` (secret scope).
  A pinned erase token anonymises only its channel's orders and carts (`erase_channel`); an unpinned one every channel.
