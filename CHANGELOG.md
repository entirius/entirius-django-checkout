# Changelog

## Unreleased

- Keys verified by django-access when installed: the storefront key (v1 `@channel_view`, v2
  `ChannelAPIKeyPermission`) and the X-API-ADMIN-KEY erase route check access tokens through
  `verify_api_key` (scopes `checkout.storefront`, `checkout.erase`), never the legacy tables. Without
  django-access nothing changes. `generate-api-key` / `generate-api-admin-key` then refuse and name
  `access_token create`; the key admins become read-only.
- Key admins show only the last four characters of a key.

## 9.3.0 — 2026-08-06

- Payment security hardening: payment endpoint throttling and PayU notify
  signature verification.
- Payment redirect bridge.
- Voucher gift passthrough and ranged bundle support.

## 9.2.0 — 2026-07-30

- Drop the withdrawn vat-validator, pricetuner, and voucher extras.
- Stewardship metadata: maintainers in `pyproject.toml`, CODEOWNERS.

## 9.1.0 — 2026-07-13

- Promote soft integrations to extras: `[returns]` (>=3.0.0), `[voucher]` (>=2.0.0).

## 9.0.0 — 2026-07-11

- Initial public release: cart, order, shipping and payment intents, discount
  rule codes, and the v2 storefront checkout API. Payment SDK integrations:
  PayU, PayPal, Autopay, Przelewy24, Paynow.
- Soft integrations behind extras: `[qms]`, `[vault]`, `[vat]`, `[pricetuner]`.
- Migrations squashed into a single initial migration for the Entirius epoch.
