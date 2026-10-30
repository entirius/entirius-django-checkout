# Changelog

## Unreleased

### Fixed

- v2 API (client-visible): `gratis.rules` of a cart lists the gift rules, as `GET carts/{id}/gratis-rules/` does. It was
  read from `gratis_rules`, a legacy field nothing fills, so it stayed empty next to `is_available: true`.

## 10.0.1 — 2026-10-27

### Added

- v2 API (client-visible addition): cart writes name why each rejected discount code was rejected. `_errors` keeps
  `ONE_OR_MORE_DISCOUNT_INVALID` and adds one detail per rejected code: `field` `codes[i].code`, a v2 code
  (`DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE`, `DISCOUNT_EXPIRED`, `DISCOUNT_MIN_ORDER_NOT_MET`, `DISCOUNT_DUPLICATE_RULE`,
  `DISCOUNT_CODE_NOT_FOUND`, …) and public meta (`code`, plus `blocking_code`, `min_order_amount`, `expired_at`,
  `active_from` or `currency` by reason). The meta carries no rule internals: no priority, and `blocking_code` only
  when the client already sees that code (it sent it, or the discount is applied in the cart). Unknown codes,
  inactive rules and other channels' codes stay `DISCOUNT_CODE_NOT_FOUND`. `CartV2Response` documents `_errors`
  in OpenAPI. Reference: `docs/api/checkout.md` (v2 cart warnings). v1 responses don't change.

### Fixed

- An automatic discount rule no longer drops out on every other cart write. A write stores the applied
  automatic discount in the cart (a rule without codes under its name), and the next write narrowed the rules to
  the codes in the cart, so the rule was gone, then back on the write after. The same narrowing dropped automatic
  rules from any cart that carries a discount code. `validate_discounts` now keeps automatic rules whatever codes
  the cart holds.
- A valid code that loses priority arbitration (an exclusive rule won, or it is exclusive and a higher-priority rule
  won) is rejected as `blocked_by_exclusive_rule` (v2 `DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE`), not as
  `code_not_found`; the internal error meta is `{code, blocking_code, blocking_priority}`, v2 shows `code` and
  `blocking_code` only. `blocking_code` is the code the customer
  entered for the winning rule, else its first code, or its name for an automatic rule without codes. Since automatic rules take part in arbitration whatever codes the
  cart holds, a combinable automatic rule with a lower `priority` blocks an exclusive code. Unknown and used-up codes
  stay `code_not_found`; v1 responses don't change.
- A second code of a rule already in the cart (or the same code sent twice) is rejected as `duplicate_rule`; it was
  dropped without a reason, so the cart reported nothing. v1 now lists it under `one_or_more_discount_invalid` too.
- An exclusive code no longer applies together with automatic rules after a write that sends items (v1 cart PUT,
  v2 PATCH items). Priority arbitration matches rules against valid cart items, but got the request items, which
  carry no status yet: no rule matched, none won, and every rule applied. `validate_discounts` now gets the
  validated items.
- A price discount adds to the discounts earlier rules already gave an item. The last item of the rule took the
  rest of the amount (for rounding) in place of its whole discount, so e.g. 5 % then 50 off came to 50 off.
- A split order no longer fails with `TypeError` on every cart: the cart's `payment_method` is a list and was passed
  to `asdict()` whole. Each part is created against the settling method, the first non-voucher one (a voucher alone:
  the voucher).
- The `voucher_insufficient_balance` error names why a voucher was refused (expired, unknown code, wrong PIN):
  `extra.voucher_errors` lists `{code, message}` per rejection and the message carries them, next to the amounts
  already in `extra`. Needs a voucher provider that returns `ErrorInfo` from `attach_to_cart` (django-checkout-voucher
  1.2.1); a provider returning plain strings keeps the old wording. `BasePaymentProvider.attach_to_cart` may now
  return `ErrorInfo` next to strings; existing providers need no change.
- An out-of-stock gratis is no longer offered nor accepted (it reached the cart as `OUT_OF_STOCK` and blocked checkout);
  stock is read for the customer, so a customer's own supplier counts. A gratis SKU without a stock row counts as out
  of stock, as for cart items.
- Two orders of one channel placed at the same moment no longer collide on their number: `in_channel_id` was
  `MAX + 1` read without a lock, so both read the same value and the second insert failed on
  `in_channel_id_unique_per_channel` (a 500 on order creation). The insert now runs in a savepoint and, on that
  collision only, reads the next number and tries again (up to 5 times); any other `IntegrityError` is raised as
  before. Nothing is locked: the payment gateway is called inside the order's transaction, so a channel lock would
  queue every order of the channel behind it. On a collision the second insert waits until the first order's
  transaction ends.

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
