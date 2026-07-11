# Checkout + Accounts — v2 Endpoint Analysis

## Address naming: `firstname` stays

Both accounts and checkout use `firstname`/`lastname` (no underscore) consistently.
Django User model uses `first_name` but it's never exposed in API directly.
Cart JSON body in DB stores `firstname`. Payment providers receive `firstname`.
React storefront has no address code yet — clean slate.

**Decision: keep `firstname`.** Document as address field naming exception in response contract.

## Cart accepts same address shape as accounts

Checkout `Address` DTO and accounts `Address` model share the same fields:

| Field           | Accounts model | Checkout DTO | Match? |
|-----------------|----------------|--------------|--------|
| `firstname`     | CharField      | str          | Yes    |
| `lastname`      | CharField      | str          | Yes    |
| `street`        | CharField      | str          | Yes    |
| `city`          | CharField      | str          | Yes    |
| `postcode`      | CharField      | str          | Yes    |
| `telephone`     | CharField      | str          | Yes    |
| `dialling_code` | CharField      | str          | Yes    |
| `country_code`  | CharField(2)   | str(len=2)   | Yes    |
| `company`       | CharField      | Optional str | Yes    |
| `tax_id`        | CharField      | (on BillingAddress) | Partial |
| `is_company`    | BooleanField   | (missing)    | **No** |
| `address_id`    | PK (int)       | Optional int | Yes    |
| `external_id`   | CharField      | Optional str | Yes    |

**Gap:** Checkout Address DTO is missing `is_company`. Accounts Address has it.
Checkout BillingAddress has `tax_id` + `requested_invoice` but not `is_company`.

v2 fix: add `is_company` to checkout address. Canonical shape in response contract.

---

## Accounts endpoints — v2 analysis

| # | v1 Endpoint                    | Method | v2 Change                                   | Priority |
|---|--------------------------------|--------|----------------------------------------------|----------|
| 1 | `customer/tokens/`             | POST   | **Minimal** — works fine. Add Pydantic schema for response, v2 error format | Low |
| 2 | `customer/tokens/refresh/`     | POST   | Same as above                                | Low      |
| 3 | `customer/tokens/blacklist/`   | POST   | Same                                         | Low      |
| 4 | `customer/tokens/validate/`    | POST   | Same                                         | Low      |
| 5 | `customer/signup/`             | POST   | Pydantic request schema, v2 error format     | Medium   |
| 6 | `customer/signup/<uid>/`       | POST   | Minimal                                      | Low      |
| 7 | `customer/password/reset/`     | POST   | Minimal                                      | Low      |
| 8 | `customer/password/reset/<key>/` | POST | Minimal                                      | Low      |
| 9 | `customer/password/change/`    | POST   | Minimal                                      | Low      |
| 10| `customer/me/`                 | GET    | **Align naming:** response uses `firstname`→keep, add null handling | Medium |
| 11| `customer/<uid>/profile/`      | GET,PATCH | Pydantic schema, consistent null handling | Medium   |
| 12| `customer/<uid>/delete/`       | DELETE | Minimal                                      | Low      |
| 13| `customer/login/<provider>/`   | GET    | **Don't touch** — OAuth redirects, works     | None     |
| 14| `customer/login/<provider>/callback/` | GET | Same                                   | None     |
| 15| `customer/tokenize/`           | GET    | Minimal                                      | Low      |
| 16| `customer/<uid>/addresses/`    | GET,PUT,PATCH,DELETE | **Key change:** canonical address shape, Pydantic, null handling, address_id → UUID | **High** |
| 17| `customer/<uid>/addresses/defaults/` | GET,POST | Align with address shape            | High     |
| 18| `customer/<uid>/addresses/files/` | GET,POST | Minimal — file upload stays         | Low      |
| 19| `wishlist/`                    | GET,POST | Standard response wrapper, Pydantic  | Medium   |
| 20| `wishlist/product/`            | POST,DELETE,PATCH | Align with product response (sku, url_key) | Medium |

### Accounts summary

- **Auth endpoints (1-9, 12-15):** Already work fine. v2 = add Pydantic schemas + v2 error format. Low priority.
- **Profile (10-11):** Consistent null handling, response wrapper. Medium.
- **Addresses (16-17):** Critical — shared contract with checkout. Canonical shape. **High priority.**
- **Wishlist (19-20):** Standard response wrapper. Medium.

---

## Checkout endpoints — v2 analysis

| # | v1 Endpoint                      | Method | v2 Change                                                     | Priority     |
|---|----------------------------------|--------|---------------------------------------------------------------|--------------|
| 1 | `POST customer/cart/`            | POST   | **Price fields:** explicit gross/net. Pydantic request schema  | **Critical** |
| 2 | `GET customer/cart/`             | GET    | Same price field rename + response wrapper                     | **Critical** |
| 3 | `GET carts/<cart_id>/`           | GET    | **Merge with #2** — one endpoint, cart_id as query param or path | **Critical** |
| 4 | `PUT carts/<cart_id>/`           | PUT    | Price fields rename. Full cart replace                         | **Critical** |
| 5 | `PATCH carts/<cart_id>/`         | PATCH  | Price fields rename. Partial update                            | **Critical** |
| 6 | `DELETE carts/<cart_id>/`        | DELETE | Minimal                                                        | Low          |
| 7 | `POST carts/<id>/merge/`        | POST   | Minimal — guest→customer merge                                 | Low          |
| 8 | `GET carts/<id>/shipping-methods/` | GET  | **Price fields:** gross/net on shipping prices                 | High         |
| 9 | `GET carts/<id>/payment-methods/`| GET    | **Price fields:** fee gross/net                                | High         |
| 10| `GET carts/<id>/delivery-points/`| GET    | Minimal — returns delivery point list                          | Low          |
| 11| `GET carts/<id>/gratis-rules/`   | GET    | Minimal                                                        | Low          |
| 12| `POST orders/`                   | POST   | **Price fields** in order body. v2 error format                | **Critical** |
| 13| `GET orders/`                    | GET    | Standard pagination (lightweight), price rename                | High         |
| 14| `GET orders/<order_id>/`         | GET    | Price rename in order body                                     | High         |
| 15| `GET orders/.../file/.../`       | GET    | Minimal — file download                                        | None         |
| 16| `POST notify/payu/`             | POST   | **Don't touch** — payment provider webhook                     | None         |
| 17| `POST notify/paynow/`           | POST   | Same                                                           | None         |
| 18| `POST notify/paypal/`           | POST   | Same                                                           | None         |
| 19| `POST notify/stripe/`           | POST   | Same                                                           | None         |
| 20| `POST notify/przelewy24/`       | POST   | Same                                                           | None         |
| 21| `POST notify/autopay/`          | POST   | Same                                                           | None         |

### Checkout price field rename (the big change)

Every item, shipping, and cart total needs gross/net split:

| v1 field (item)            | v2 field                    | Notes                          |
|----------------------------|-----------------------------|--------------------------------|
| `base_unit_price`          | `unit_gross`                | Catalog price per unit         |
| `base_total_price`         | `total_gross`               | Catalog price × qty            |
| `unit_price`               | `final_unit_gross`          | After discounts, gross         |
| `total_price`              | `final_total_gross`         | After discounts × qty, gross   |
| `unit_price_netto`         | `final_unit_net`            | After discounts, net           |
| `total_price_netto`        | `final_total_net`           | After discounts × qty, net     |
| `special_unit_price`       | `special_unit_gross`        | Promo price per unit           |
| `special_total_price`      | `special_total_gross`       | Promo × qty                    |
| `unit_tax_amount`          | `unit_tax`                  | Tax per unit                   |
| `total_tax_amount`         | `total_tax`                 | Tax × qty                      |
| `tax_rate` (int 23)        | `tax_rate` ("0.23")         | Decimal string, matches Matrix |
| `discount_amount`          | `discount_gross`            | Discount amount gross          |
| `discount_amount_netto`    | `discount_net`              | Discount amount net            |

Cart-level totals:

| v1 field                   | v2 field                    |
|----------------------------|-----------------------------|
| `base_total`               | `subtotal_gross`            |
| `total`                    | `total_gross`               |
| (computed)                 | `subtotal_net`              |
| (computed)                 | `total_net`                 |
| `total_tax`                | `total_tax`                 |
| `fee_price`                | `fee_gross`                 |
| `fee_tax_price`            | `fee_tax`                   |
| `fee_tax_rate` (int)       | `fee_tax_rate` ("0.23")     |

### Checkout summary

- **Cart CRUD (1-5):** Critical — price field rename, Pydantic schemas, response wrapper
- **Cart options (8-11):** High — shipping/payment price alignment
- **Orders (12-14):** Critical — price fields in order body
- **Webhooks (16-21):** Don't touch — external provider contracts
- **Merge/delete (6-7):** Low — minor cleanup

---

## Cross-module dependencies

```
Frontend (React storefront)
    ├── Matrix v2  → product prices (gross/net)
    ├── Accounts v2 → addresses (firstname, canonical shape)
    └── Checkout v2 → cart prices (gross/net), addresses (same canonical shape)
```

Front sees consistent price naming across Matrix and checkout.
Front sees consistent address shape across accounts and checkout.

## Recommended execution order

1. **api-response-contract.md** — add Address canonical shape, document `firstname` exception
2. **Matrix v2** — implement (highest value, catalog is 90% of storefront traffic)
3. **Accounts v2 addresses** — canonical address shape, aligns with response contract
4. **Checkout v2 cart/order** — price field rename, uses canonical address
5. **Accounts v2 rest** — auth endpoints, wishlist (lowest priority)
