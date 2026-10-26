# Changelog

## 10.0.1rc1

## 10.0.0 — 2026-10-12

- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).
- Keys verified by django-access when installed: the storefront key (v1 `@channel_view`, v2
  `ChannelAPIKeyPermission`) and the X-API-ADMIN-KEY erase route check access tokens through
  `verify_api_key` (scopes `checkout.storefront`, `checkout.erase`), never the legacy tables. Without
  django-access nothing changes. `generate-api-key` / `generate-api-admin-key` then refuse and name
  `access_token create`; the key admins become read-only (no add, change or delete).
  Without django-access, keys come only from those commands; the admin masks keys, so an admin-added key can't be read back.
- Key admins show only the last four characters of a key.
- **Breaking for integrators:** with django-access, a channel-pinned erase token anonymises only the orders and carts
  of the URL's channel (`Order.channel` / `Cart.channel`, both for the customer e-mail and the body match); an e-mail
  found only elsewhere answers the unknown-e-mail 404. Legacy erase keys are imported as tokens pinned to their
  channel, so after the upgrade they erase only there. An unpinned token, and the path without django-access, still
  erase in every channel. `CustomerService.anonymize_customer(email, channel=None)`; `customer_anonymized_signal` carries `channel_idx`
  (None = every channel) — a receiver scrubbing by e-mail must scope to it.
- OpenAPI: the admin order list is a ViewSet (same URL, URL name and payload), generated as
  `checkout_v2_admin_orders_list` with its paginated response; the storefront path parameters are described.

### Security

- Autopay: the notification body is parsed by `defusedxml` (new dependency `defusedxml>=0.7.1`). A document that
  declares an entity (internal or external) is refused before anything is expanded and takes the
  parse error path, so the answers of the view are unchanged. The view is public and parsed the body with the
  standard library before the hash check, so an entity-expansion document cost the worker memory and time.
- The payment and pay-code throttles count the client, not the reverse proxy: with DRF's `NUM_PROXIES` set the
  address comes from the `X-Forwarded-For` entries the trusted proxies appended (`0` = no proxy, the header is
  ignored); unset, the TCP peer address is used as before. Behind a proxy the whole shop shared one allowance, so
  ordinary traffic could exhaust it. Where the request names a cart, the allowance is counted per client and cart.
- PayU: a PayU method without `second_key` is reported by the system check `django_checkout.W001`
  (`manage.py check --database default`, also printed by `migrate`), and a notification refused for that reason is
  logged once per method at warning level, without the payload.

### Upgrade notes

- **PayU:** every PayU payment method (`payu`, `payu_card`, `payu_blik`) needs `second_key` (the second key of the
  POS in the PayU merchant panel) in its `additional_data`. Since 9.3.0 a notification that cannot be verified is
  answered 403, so a method configured before that confirms no payment until the key is set. Run
  `manage.py check --database default` after upgrading: `django_checkout.W001` lists the methods that lack it.
- **Behind a reverse proxy:** set DRF's `NUM_PROXIES` to the number of trusted proxies in front of the service;
  without it the throttles see the proxy as the one client of the whole shop.

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
