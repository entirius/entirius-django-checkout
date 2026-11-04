# Changelog

## Unreleased

### Security

- A `free_order` payment method settles only a cart with nothing left to pay (after vouchers and fees). With an
  amount due the cart is invalid with `free_order_amount_due` (v2 `FREE_ORDER_AMOUNT_DUE`, client-visible
  addition), the payment method list no longer offers `free_order`, and `Order.create` refuses on its own. Customer
  groups and product links still narrow who sees the method; they no longer make a paid cart free.
- PayPal: `notify/paypal/` changes a payment only when PayPal confirms the notification's signature
  (verify-webhook-signature, against the `webhook_id` in the payment method's `additional_data`) and the payment
  belongs to a PayPal payment method. Anything else is answered 403 and writes nothing; PayPal redelivers it. The
  verification calls PayPal with a 10 s timeout. `django_checkout.W002` (`manage.py check --database default`)
  lists PayPal methods without `webhook_id`.
- PayPal: the webhook, the buyer's return and the cancel return only move a payment forward. A COMPLETE payment
  stays final, a CANCELLED one gives way only to a completed payment; an order is confirmed only while it waits for
  payment (or after a cancellation: logged for manual review, its stock reservation is gone) and cancelled only while
  it waits for payment. Statuses are applied under a row lock on the payment and its order. The cancel return
  records a cancellation only: for an approved or completed PayPal order it changes nothing and captures nothing.
  BI finish tags keep their wording; new outcomes add their own (`..., Order status <status> kept`,
  `PaymentIntent is already <status>, <status> ignored`).
- Przelewy24: a verified notification completes the payment through the same rules: a resent notification of a
  paid transaction no longer puts an order in progress, completed or returned back to CONFIRMED. The BI finish tag
  of a confirmed order keeps its wording.
- Paynow: notifications go through the same rules: a late or repeated status no longer pulls a COMPLETE payment
  back, re-confirms an order that moved on, or cancels an order confirmed by hand. BI finish tags keep their wording.
- Paynow: `notify/paynow/` changes only a Paynow payment (another gateway's payment is answered 404, like any other
  refused notification), and the `Signature` header is compared in constant time.
- Przelewy24: `notify/przelewy24/` changes only a Przelewy24 payment: a notification naming another gateway's
  payment is answered 403 before Przelewy24 is asked, and a body needs both `orderId` and `sessionId`. The verify
  call uses the payment's own method, not the first method listed on the order (with a voucher listed first it
  failed).
- Przelewy24: a notification is verified by its `sign` (SHA-384 over the documented fields and the method's CRC key)
  before Przelewy24's verify API is asked; a missing CRC key or sign is answered 403. The CRC key comes from the
  method's `crc_key`, else the `PRZELEWY24_CRC_KEY` setting, for the sign and the verify call alike.
- Autopay: a repeated or late `SUCCESS` ITN confirms an order only while it waits for payment, through the same
  rules; an order in progress, completed or returned keeps its status. A COMPLETE payment was already never
  downgraded. BI finish tags keep their wording.
- Autopay: `notify/autopay/` verifies an ITN only with a non-empty `AUTOPAY_HASH_KEY` entry of the channel in the
  URL; without one the ITN is answered 400 `NOTCONFIRMED` (it was 500 for a channel missing from the setting). The
  hash is compared in constant time.
- Autopay: a verified ITN changes only an Autopay payment of the channel whose hash key verified it.
- Stripe: checkout session events go through the same rules: a late or repeated event no longer pulls a COMPLETE
  payment back, re-confirms an order that moved on, or cancels an order confirmed by hand and releases its stock.
  An unhandled `payment_status` on an open payment still sets ERROR and answers 400. BI finish tags keep their
  wording.
- PayU: signed notifications (`payu`, `payu_card`, `payu_blik`) go through the same rules: a late or repeated status
  no longer pulls a COMPLETE payment back, re-confirms an order that moved on, or cancels an order confirmed by hand
  and releases its stock. BI finish tags keep their wording.
- Stripe: `notify/stripe/` verifies an event only with a non-empty `endpoint_secret` of the channel's Stripe
  payment method; without one the event is answered 400. The view no longer sets the process-wide
  `stripe.api_key`, and a request without a `Stripe-Signature` header is answered 400 instead of 500.
- Stripe: a verified event changes only a Stripe payment of the channel whose endpoint secret verified it.

### Upgrade notes

- **Przelewy24:** notifications are refused (403) for a Przelewy24 method without a CRC key: set `crc_key` in its
  `additional_data` or the `PRZELEWY24_CRC_KEY` setting. Registering transactions already needed it for their sign.
- **PayPal:** every PayPal payment method needs `webhook_id` (PayPal developer dashboard: the REST app, Webhooks,
  Webhook ID of the webhook pointing at `notify/paypal/`) in its `additional_data`. Without it webhook notifications
  are refused (PayPal retries them for about 3 days) and an order is confirmed only when the buyer returns to the
  shop.
- **Customer erasure:** `DELETE customer/delete` answers 409 `OPEN_ORDERS` (and the Python API raises
  `django_checkout.output.admin.ErasureBlocked`) while the customer has an order that is not `complete`, `returned`
  or `canceled`. A caller that erases on request has to ship or cancel those orders first and repeat the request.
  Erase in checkout before deleting the account in django-accounts: afterwards checkout finds orders only by the
  e-mails they hold.

### Fixed

- Customer erasure (GDPR, `DELETE customer/delete` and `output.admin.admin_customer_delete`) anonymises every
  personal field checkout keeps, not only the names and e-mails inside `order_body`: the order's `billing_email` /
  `shipping_email` columns and the e-mails of its `UsedCoupon` rows; street, city, postcode, phone, company and tax id
  of both addresses, the customer's comment and IP (in the order and in its cart); and the gateway payloads of its
  payments (`PaymentIntent.provider_request` / `provider_notify`: buyer, payer, shipping address), which keep only
  ids, amounts, currencies, statuses and timestamps. The intent's columns (amount, currency, external id, status)
  stay. The payment redirect bridge's target (`PaymentRedirect.target_url`, a copy of the `continue_url` the body
  already redacted) becomes `***REDACTED***`, so a link still open answers 404. The request also finds orders by those e-mail columns and by the e-mail a used code stored (the account
  e-mail of a deleted account), so repeating it completes an order an earlier erasure left half done. New command
  `complete-erasures` finishes the orders and carts erased before this release (dry run unless `--apply`).
- Log lines and BI events no longer carry a customer's personal data. The admin order preview (v2
  `GET admin/{channel}/orders/{uid}/`) logged the billing e-mail and now logs the order id; the PayU, PayPal and
  Stripe notification views logged and sent to BI the whole notification (buyer, payer, `customer_details`) and now
  keep only what `scrub_gateway_payload` keeps (ids, amounts, statuses), a body that is no JSON not at all; the
  Autopay ITN view no longer logs its XML (`customerData` is the buyer); a failed v1 order logs the customer's pk, not
  `str(customer)` (the username, which is the e-mail); Paynow's invalid-phone line no longer names the number.
- Stripe: a line of several units with a cart discount is charged its discounted total. Its `unit_amount` was the
  unit price less the discount of the whole line, and Stripe multiplies `unit_amount` by the quantity: 3 x 54.99 at
  15 % off charged 90.72 instead of 140.22, and a large discount sent a negative amount Stripe refused. Lines carry
  the sold unit price; what the cart takes off its items (cart discounts, a gift's price, a voucher taken off the
  total) goes into one `amount_off` coupon per session, single use, in the order's currency, on the payment method's
  account. A gift was charged its product's full price, and a voucher on top of the card. The coupon is named by
  `localized_names.discount` in the method's `additional_data` (default `Discount`).
- A voucher on a cart that splits into several orders is refused (client-visible). The cart reports
  `voucher_not_supported_on_split_order` (v2 `_errors` code `VOUCHER_NOT_SUPPORTED_ON_SPLIT_ORDER`, v1 `extra`
  `split_parts`) and stays `invalid`; `POST orders/` refuses it with v2 409 and that `error`, v1 403 and that
  `status`. Each part of a split is created with the gateway alone, so the voucher reached none of the parts: they
  added up to the full amount and the voucher kept its balance. A split without a voucher and a voucher without a
  split are not affected.
- An order paid with a voucher and another method debits the voucher before any gateway is asked: with the gateway
  listed first its session existed before the debit, and a debit that failed rolled the order back while the session
  stayed payable. Cash on delivery and free order come from the method that pays the rest, not from entry `[0]`: a
  voucher with cash on delivery left the order `unpaid` instead of `confirmed`. The free-order check of
  `Order.create` reads that method too.
- A gateway paid next to a voucher (`payment_method` `[voucher, gateway]`) reads its own entry of the list, not
  entry `[0]`, which is the voucher's. PayU (`payu`, `payu_card`, `payu_blik`) and Stripe got no return URL
  (`continueUrl`, `success_url`; Stripe refuses a session without one) and PayU card no card token. BLIK sent the
  customer's BLIK code on no order, with or without a voucher: it looked for `pay_code` on the list itself. PayPal's
  breakdown carries the voucher in `discount`: the amount is net of the voucher and the parts were not, so PayPal
  refused the order.
- `cheapest_gratis` and `most_expensive_gratis` give their discount (client-visible: cart totals drop). The rule's
  units of the chosen item (`extra_value`, at most what the cart holds) come at the gift price, as a picked gift does
  (`GRATIS_PERCENT_DISCOUNT` down to `GRATIS_PRICE`, or `GRATIS_PRICE` under `GRATIS_MECHANISM = 2`), never above
  the item's own price; a channel that discounts net prices gets the same gift price. The discount was computed and
  then overwritten with the item's earlier discount, so the code came out valid at 0. The item is picked by price
  among the bought lines: a gift line, always the cheapest at the gift price, is never picked, and a line with more
  units no longer wins over a cheaper (or dearer) one; quantity only breaks a price tie.
- Merging a guest cart into the customer's cart no longer fails, v1 (`carts/{id}/merge/`) and v2
  (`POST carts/{id}/merge/`). `CartRequest.factory()` left out five fields the request requires
  (`custom_order_id`, `requested_delivery_date`, `split_order`, `affiliate_code`, `customer_ip`), so every merge
  raised `TypeError`: v2 answered 500, v1 500 `Internal Exception`. A gift line is no longer summed into the merged
  quantities as a bought unit: the customer's gift comes back from its code, and a guest's gift goes with the guest's
  codes, which a merge does not carry over. A guest cart that already became an order (or is no longer open) answers
  404 and stays as it is; deleting it after the merge raised `ProtectedError`, another 500. v1 merges through the
  same service as v2, so it keeps each line's `extra` and `voucher_gift` as v2 does.
- The PayPal `webhook_id` warning (`django_checkout.W002`) reaches the CMS: it carries the `entirius_config` tag
  django-munin collects, and names the methods in its hint.
- Stripe: `notify/stripe/` stores the checkout session as plain JSON. With stripe 12 or later (the lock pins 15) a
  `StripeObject` is no dict, so saving it in `provider_notify` raised and every verified session event answered
  500 without changing the payment.
- PayPal: the buyer's return (`return/paypal/`) saves the status PayPal's capture API reports on the payment and
  the order. It answered 500 after the capture and saved nothing, so only the webhook confirmed PayPal orders.
- A gift of a SKU the customer also buys is a line of its own: it no longer replaces the bought line (a bought mug
  and the same mug picked as the gift came out as one mug at the gift price, `changed_price`). The gift is refused
  when the bought units and the gift together exceed the saleable stock, and a gift above the stock is no longer
  accepted on its own either. Cart weight and volume count the pieces of every line; each line's `total_weight` is
  its own.
- `import-discount-rules` (and the CSV import in the Django admin) no longer hangs. The mode-of-action loop of a
  row never ended once the row ran out of modes (`is_inclusion_or_exclusion_<n>` empty or missing), so the import
  never reached the row's codes or the next row; the admin import held the request until it timed out.
- An order is refused when a valid item is priced 0 (`UNPROCESSABLE_ENTITY sku: <sku>`, v1 403). `OrderValidation`
  compared `str(total_price) == 0`, a string against an int, so the check never fired and such an item went into
  the order. A gift still passes at 0 (`GRATIS_MECHANISM = 2`, `GRATIS_PRICE = 0`), and an item that is not valid
  keeps its own reason (`OUT_OF_STOCK`), not the 0 that `invalid_factory` gives it.
- A v1 order with a code that sits on more than one rule (one code on an EUR and a PLN rule, as `GRATIS_STEP` in
  the test package, or the same code in another channel) no longer fails after the order is saved. `use_discounts`
  looked the code up by its text alone and raised `MultipleObjectsReturned`, so the customer got 403
  `INVALID_REQUEST` for an order that existed. The usage now counts on the code row the cart applied: a rule of the
  order's channel and currency (or of none), of the same kind (typed or automatic) and modifier, lowest pk on a tie.
- v2 API (client-visible): `POST orders/` counts the discount codes it takes, as v1 does. It never did, so
  `max_used` and `max_uses_per_user` limited nothing for v2 orders and a one-use code worked forever. v1 and v2 now
  count through one function (`services/discount_usage.py`), in the order's transaction, with an update that takes a
  use only while one is left: of two orders racing for the last use, one is not saved. A v2 order whose cart holds a
  code that no longer applies (used up, the per-address limit reached, expired, ...) is refused with 409 and the
  code's reason as `error` (`DISCOUNT_USAGE_LIMIT_REACHED`, ...) instead of being placed without the discount at a
  total the customer never saw. v1 keeps placing such an order without the code. A v1 order that lost the last use,
  or took an automatic rule whose code was at `max_used`, no longer answers 403 for an order that was saved; the
  first is not saved, the second is placed and its code stops counting at the limit. The floor of
  `entirius-django-utils` moves to 2.1.0 (the v2 handler keeps a 409's code).
- A discount rule limited to some currencies (`currencies`) applies only to carts in one of them; a rule with none
  applies in every currency, as before. Only the gratis offer list read the field, so a PLN-only automatic rule
  discounted an EUR cart, a PLN-only rule could win priority arbitration on an EUR cart and block its codes, and a
  code on an EUR and a PLN rule (`GRATIS_STEP` in the test package) took the PLN rule on an EUR cart and offered no
  gift. A code whose rules all leave out the cart currency is rejected as `currency_not_supported` (v2
  `DISCOUNT_CURRENCY_NOT_SUPPORTED`, meta `currency`); v1 lists it under `one_or_more_discount_invalid`.
- A bundle counts all of its components when several bundles are checked at once. The links were grouped by
  adjacent rows of an unordered query, so a bundle whose links came apart kept only the last group of them.
- v2 API (client-visible): `gratis.rules` of a cart lists the gift rules, as `GET carts/{id}/gratis-rules/` does. It was
  read from `gratis_rules`, a legacy field nothing fills, so it stayed empty next to `is_available: true`.
- v2 OpenAPI (client-visible, schema only): `POST orders/` lists `payment_error` in `OrderCreateResponse`. The 201 body
  has carried `payment_error: true` when the order was created but the payment provider failed, and the schema did
  not name it. The body is unchanged: the field is there only on a failed payment.
- v2 OpenAPI (client-visible, schema only): the guest routes (carts and their steps, `countries/`, `POST orders/`)
  declare the channel key without the customer JWT next to the key with it. They declared
  `[{ApiKeyAuth: [], jwtAuth: []}]`, both required, though a guest works with `X-API-KEY` alone, so the schema
  could not tell them from the customer routes (`orders/list/`, `carts/latest/`, merge), which still need both.
  Access does not change.
- v2 API (client-visible): `POST orders/` on a cart that cannot become an order yet answers 409 with the reason in
  `error` (`ADDRESSES_NOT_SELECTED`, `SHIPPING_OPTION_NOT_SELECTED`, `PAYMENT_OPTION_NOT_SELECTED`, `OUT_OF_STOCK`).
  It answered 500 `INTERNAL_ERROR`, because `OrderValidation` raises a v1 exception the v2 handler does not know, so a
  customer whose item sold out during checkout got no reason. The v1 response stays 403.
- v2 API (client-visible): `subtotal_gross` and `subtotal_net` of a cart are the items alone, as the schema says
  ("before shipping"). They were taken from `base_total`, which adds shipping before its reduction and the payment fee,
  so the subtotal moved when the customer chose a shipping method: 100.00 of items with free shipping answered
  `subtotal_gross` 109.99 next to `total_gross` 100.00. `total_gross`, `total_net` and `total_tax` don't change, and
  neither does `base_total` in the v1 response.

### Added

- Docs: `docs/api/checkout.md` (Code usage) says what an order that loses the race for a code's last use leaves
  behind. It has already opened a payment session at the gateway when it rolls back; the customer never gets the
  session, no money moves, and the gateway keeps it as an abandoned, unpaid session.
- A discount code is unique per (channel, currency): two rules that share a channel and a currency (a rule
  without currencies holds every currency) may not hold the same code. The same code on rules of other channels
  or of other currencies stays allowed (`GRATIS_STEP` on an EUR and a PLN rule). Admin API v2 (client-visible
  addition): creating or renaming a clashing code, a rule created with one, or new `channels` / `currencies` that
  make a rule's code clash with a row it did not clash with before answer 400 `VALIDATION_ERROR` with the detail
  on `code`, `codes.<i>.code`, `currencies` or `channels` and issue `UNIQUE`; nothing is written. The Django admin
  (code page, the rule's codes inline) refuses the same, and `import-discount-rules` skips a clashing code (and a
  row whose new channels clash) with a warning. Clashes stored before keep working, and an edit that does not
  touch them goes through. No migration and no database constraint: the new configuration check
  `checkout.discount_codes` (`manage.py check --database default --tag checkout.discount_codes`, and the CMS
  through django-munin) lists the stored ones per channel. Reference: `docs/api/discounts.md`.
- `django_checkout.output.quantities.get_bundle_quantities(skus, channel_idx)`: how many of each bundle its
  components allow, as the cart counts a bundle line (the bundle's own Stock row does not decide; with limits, the
  required and default components). For other modules that answer stock, so a bundle they report available is one
  the cart accepts. SKUs that are not bundles are left out.

### Changed

- Customer erasure (client-visible) is refused while the shop still has to fulfil one of the customer's orders: one
  in `new`, `unpaid`, `confirmed`, `holded` or `in_progress`. Nothing is anonymised then, not the other orders, not
  the carts; v1 answers 409 `OPEN_ORDERS` with the blocking orders' ids, numbers and statuses (no personal data), and
  `output.admin.admin_customer_delete` raises `ErasureBlocked`. It anonymised the delivery address of an order still
  to ship. A pinned erase token looks at its channel's orders only. `complete-erasures` is not affected.

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
