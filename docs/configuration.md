# Configuration

## Settings reference

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
