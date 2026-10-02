# AGENTS.md

Checkout engine for Django: carts, orders, payment intents, shipping, discounts and sale offers —
distribution `entirius-django-checkout`, Django app `django_checkout`.

Handles cart lifecycle, order creation, payment provider integration (PayU, PayPal, Autopay,
Przelewy24, Paynow, Stripe), discount codes, shipping/payment method selection, stock reservation
and order splitting.

Tech: Django 5, DRF, Pydantic (v2 API), marshmallow (v1 API), PostgreSQL.

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |

## Conventions

- English only: code, docs, commits, branches, PRs.
- MPL-2.0: every non-trivial source file carries the license header (pre-commit inserts it).
- Toolchain: uv + ruff + hatchling + pytest; all config in `pyproject.toml`; `uv.lock` committed.
- Git flow: `master` (production) + `develop` (integration); changes land via PR; semver tag on `master`.
- Never rename the package / Django app_label / DB table prefix `django_checkout` — it is a schema contract.
- Migrations are part of the public contract — never edit an already released migration.
- Default: do not commit — git is the user's call.

## Commit Message Format

**NEVER add `Co-Authored-By: Claude ...` (or any other Claude/Anthropic attribution) to commit messages.**

This overrides the default Claude Code behavior of appending a `Co-Authored-By` trailer. Commit messages MUST contain only the user's authored content — no robot footer, no "Generated with Claude Code" line, no co-author trailer.

Same rule applies to PR descriptions: no `Generated with [Claude Code]` footer.

## Architecture

```
src/django_checkout/
├── models/                  # 35 Django models (ORM layer)
│   ├── cart.py              # Cart with JSONField cart_body
│   ├── order.py             # Order with JSONField order_body
│   ├── channel.py           # Checkout channel config (discount mode, split rules)
│   ├── payment_method.py    # Payment methods per channel (11 providers)
│   ├── shipping_method.py   # Shipping methods per channel
│   ├── shipping_option.py   # Country/currency-specific shipping prices
│   ├── discount_code.py     # Discount codes (usage limits, dates)
│   ├── discount_rule_code.py # Discount rules (15 modifier types)
│   ├── stock.py             # Stock per product per supplier
│   └── ...
├── domain/                  # Business logic (v1/v2 shared)
│   ├── cart.py              # process_cart() — main processing pipeline
│   ├── dto/                 # Marshmallow dataclass DTOs
│   │   ├── cart.py          # CheckoutData, CartRequest, CartResponse
│   │   ├── item.py          # ItemData, ValidatedItemData (30+ price fields)
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

## Data Model

| Entity | Key Fields | Relationships |
|--------|-----------|---------------|
| Cart | cart_id (UUID), cart_status, cart_body (JSON) | -> Channel, -> Customer |
| Order | order_id (UUID), pretty_id, order_status, order_body (JSON) | -> Cart, -> Channel, -> Customer |
| Item | sku, quantity, unit_price, total_price | -> Order, -> ProductRepresentation |
| Channel | idx (unique), discount_apply_type, split config | -> Language, -> Currency, -> Country (FK+M2M) |
| PaymentMethod | code, provider, fee_type, fee_value | -> Channel, -> Countries M2M |
| ShippingMethod | code, method_type, price_type | -> Channel |
| ShippingOption | price_brutto, tax_rate, free_delivery_above | -> ShippingMethod, -> Country, -> Currency |
| DiscountRuleCode | modifier (15 types), target, priority | -> Channels M2M |
| DiscountCode | code, max_used, active_from/to | -> DiscountRuleCode |
| Stock | quantity, saleable_quantity_limit | -> ProductRepresentation, -> Supplier |
| ProductRepresentation | sku (unique per channel) | -> Channel |

Cross-app FK/M2M: `django_accounts` (Customer, Group), `django_pim` (Product, Attribute, Feature,
ProductCategory), `django_regional` (Country, Currency, Language).

## API Surface

### v1 (function-based views, marshmallow DTOs)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout/1/{channel}/carts/` | Create cart |
| GET | `/api/checkout/1/{channel}/customer/cart/` | Latest customer cart |
| GET/PATCH | `/api/checkout/1/{channel}/carts/{id}/` | Get/update cart |
| GET | `/api/checkout/1/{channel}/carts/{id}/shipping-methods/` | Available shipping |
| GET | `/api/checkout/1/{channel}/carts/{id}/payment-methods/` | Available payment |
| GET | `/api/checkout/1/{channel}/carts/{id}/gratis-rules/` | Free product rules |
| POST | `/api/checkout/1/{channel}/carts/{id}/merge/` | Merge guest cart |
| POST/GET | `/api/checkout/1/{channel}/orders/` | Create/list orders |
| GET | `/api/checkout/1/{channel}/orders/{id}/` | Order detail |
| POST | `/api/checkout/1/{channel}/notify/{provider}/` | Payment webhooks |

### v2 (DRF APIViews, Pydantic schemas — in progress)

Sub-resource PATCH pattern. Each PATCH sends one concern, returns full cart.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout/v2/{channel}/carts/` | Create cart |
| GET | `/api/checkout/v2/{channel}/carts/{id}/` | Cart detail |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/items/` | Update items |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/addresses/` | Set addresses |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/discounts/` | Apply discount codes |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/shipping/` | Select shipping |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/payment/` | Select payment |
| POST | `/api/checkout/v2/{channel}/orders/` | Create order |
| GET | `/api/checkout/v2/{channel}/orders/` | List orders |

v2 price fields use explicit gross/net naming (aligned with Matrix v2).

## Soft integrations (optional extras)

| Extra | Package | Used for |
|---|---|---|
| `qms` | entirius-django-qms | stock sync command reads QMS warehouse/xray sources |
| `vault` | entirius-django-vault | card-on-file storage for PayU card payments |
| `returns` | entirius-django-returns | returnable-orders lookup in `views/order.py` |

Own satellite `django-returns` is imported lazily with graceful fallback; it depends on this
package (never the other way round), hence an extra — not a runtime dep.

Further integration points degrade gracefully when the integrating package is absent:
VAT-id validation (`TURN_ON_VIES_VALIDATION_AND_0_VAT_WHEN_VALID_AND_OUTSIDE_PL`), customer-group
pricing (`USE_PRICE_TUNER_IN_CHECKOUT`) and the voucher payment provider + validation signals
(`USE_VALIDATE_VOUCHERS_SIGNAL`, `validate_vouchers_signal`, `compute_cart_voucher_total_signal`)
are lazy imports / signal emissions with no-op fallbacks — the integrating packages ship outside
this repository.

## Settings Reference

Module settings live in `django_checkout/settings.py`, read via `getattr(settings, ..., default)`.
Fail-fast (raise at import when unset): `PRIVATE_DIR`, `EXPORT_DIR`; `django_checkout.bi` requires
`BI_ENVIRONMENT` and `BI_BUSINESS_UNIT`.

Key toggles (defaults in parentheses): `USE_PRICE_TUNER_IN_CHECKOUT` (False),
`IS_VAT_OSS_TURN_ON` (False), `COUNTRY_CHECKOUT_MECHANISM` (0),
`TURN_ON_VIES_VALIDATION_AND_0_VAT_WHEN_VALID_AND_OUTSIDE_PL` (False),
`USE_VALIDATE_ITEMS_SIGNAL` (True), `USE_VALIDATE_VOUCHERS_SIGNAL` (False — flip to True in
services that install django-checkout-voucher), `CUSTOMS_THRESHOLD_ENABLED` (False),
`USE_CACHED_VIEWS` (False), `ADD_MAX_AVAILABLE_QUANTITY` (True), `GRATIS_MECHANISM` (1),
`AUTOPAY_HASH_KEY` (None), `GLOBAL_STOCK_RESERVATION_CHANNELS` ([]; a single-element list raises).

## Testing

```bash
uv run pytest                              # all tests
uv run pytest -x                           # stop on first failure
DATABASE_URL=postgresql://... uv run pytest  # custom database
```

Tests need PostgreSQL (default `postgresql://postgres:postgres@localhost:5432/test`).

## Gotchas

- `cart_body` JSONField is the shared contract between v1 and v2. Both read/write the same blob.
- `process_cart()` in `domain/cart.py` is the main processing pipeline — shared by v1 and v2.
- v1 and v2 URLs coexist. v2 patterns MUST be before v1's `<str:version>` in `urls.py`.
- Payment webhooks stay v1 only (external provider contracts).
- `tax_rate` is integer `23` in v1 domain, decimal string `"0.23"` in v2 response.
- Discount modifier types: 15 variants including stepped, gratis, percentage, fixed.
- Order splitting creates multiple orders from one cart based on feature/attribute rules.
- Keys: `utils/api_keys.py` `key_is_valid` is the one check (v1 decorators, v2 permission). With
  `django_access` installed it calls `verify_api_key` (scopes `checkout.storefront` / `checkout.erase`) and never
  reads `APIKey` / `APIAdminKey`; legacy keys work only as imported tokens. Soft dependency — never in `pyproject.toml`.

## Management commands

| Command | Description |
|---------|-------------|
| `generate-api-key` | Generate APIKey for channel (refuses when django-access is installed) |
| `generate-api-admin-key` | Generate admin APIKey (refuses when django-access is installed) |
| `import-discount-rules` | Bulk import discount rules |
| `export-discount-rules` | Export discount rules |
| `import-limit-for-products` | Import product shipping/payment limitations |
| `import-link-for-products` | Import product shipping/payment linkages |
| `import-matrix-shipping-price` | Import weight-based shipping matrices |
| `import-details-for-checkout-product` | Import product details |
| `checkout-qty-to-csv` | Export stock quantities |
| `sync-checkout-stock-from-qms` | Sync stock from QMS sources (extra `qms`) |
