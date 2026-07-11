# Field Analysis: Accounts vs Checkout vs Matrix v2

## 1. Address fields — accounts vs checkout

Side-by-side comparison. Both modules handle addresses — must be one canonical shape.

| Field             | Accounts model   | Accounts API response | Checkout DTO    | Match? | v2 canonical     |
|-------------------|------------------|-----------------------|-----------------|--------|------------------|
| `firstname`       | CharField        | `"firstname"`         | `firstname: str`| Yes    | `firstname`      |
| `lastname`        | CharField        | `"lastname"`          | `lastname: str` | Yes    | `lastname`       |
| `street`          | CharField        | `"street"`            | `street: str`   | Yes    | `street`         |
| `city`            | CharField        | `"city"`              | `city: str`     | Yes    | `city`           |
| `postcode`        | CharField        | `"postcode"`          | `postcode: str` | Yes    | `postcode`       |
| `telephone`       | CharField        | `"telephone"`         | `telephone: str`| Yes    | `telephone`      |
| `dialling_code`   | CharField        | `"dialling_code"`     | `dialling_code` | Yes    | `dialling_code`  |
| `country_code`    | CharField(2)     | `"country_code"`      | `country_code`  | Yes    | `country_code`   |
| `company`         | CharField        | `"company"`           | `company: Optional` | Yes | `company`       |
| `is_company`      | BooleanField     | `"is_company"`        | **missing**     | **No** | `is_company`     |
| `tax_id`          | CharField        | `"tax_id"`            | on BillingAddress | Partial | `tax_id`       |
| `email`           | not on Address   | not in address response | on SimplifiedAddress | — | on billing only |
| `requested_invoice` | not in accounts | —                    | on BillingAddress | — | billing only    |
| `address_id`      | PK (int as str)  | `"address_id": "42"` | `Optional[int]` | Type mismatch | UUID or int? |
| `external_id`     | CharField        | `"external_id"`       | `Optional[str]` | Yes    | `external_id`    |
| `attachments`     | AddressFile list | `"attachments": [...]`| not in checkout | —      | accounts only    |
| `source`          | IntEnum          | not in response       | not in checkout | —      | internal only    |

### Gaps to fix in v2

1. **`is_company`** — accounts has it, checkout doesn't. Add to checkout Address DTO.
2. **`tax_id`** — accounts has it on Address, checkout has it on BillingAddress (subclass). Align: both on base address, `null` when not applicable.
3. **`email`** — checkout puts it on SimplifiedAddress. Accounts doesn't have email on address at all (email is on Customer). v2: `email` on billing_address only (for order confirmation), not on base address.
4. **`address_id`** — accounts returns PK as string `"42"`. Checkout accepts `Optional[int]`. v2: decide — keep int or move to UUID. Keeping int is simpler (no migration).

### v2 canonical Address shape

```jsonc
{
  "address_id": 42,                    // int (accounts PK, referenced by checkout)
  "firstname": "John",
  "lastname": "Doe",
  "street": "Main St 1",
  "city": "Warsaw",
  "postcode": "00-001",
  "country_code": "PL",               // ISO 3166-1 alpha-2, uppercase
  "telephone": "123456789",
  "dialling_code": "+48",             // always starts with +
  "company": null,                     // string or null
  "is_company": false,                 // bool
  "tax_id": null,                      // string or null
  "external_id": null                  // string or null
}
```

BillingAddress extends with:
```jsonc
{
  "...base address fields...",
  "email": "john@example.com",         // for order confirmation
  "requested_invoice": false,
  "vat_validation_status": false
}
```

---

## 2. Customer profile — current vs proposed

### Current accounts profile response

```jsonc
{
  "customer_id": "uuid",
  "firstname": "John",                 // from user.first_name, renamed without underscore
  "lastname": "Doe",                   // from user.last_name, renamed without underscore
  "email": "john@example.com",
  "sex": "male",                       // male | female | other | null
  "language": "en",                    // Language.iso2 or null
  "extra": {}                          // whitelisted extra data
}
```

### Missing fields for a proper user profile

| Field         | Source                    | Why needed                              |
|---------------|---------------------------|-----------------------------------------|
| `avatar_url`  | New field on Customer     | User avatar for storefront UI           |
| `nickname`    | New field on Customer     | Display name (alternative to real name) |
| `phone`       | Customer.phone (exists!)  | Already in model, not in API response   |
| `area_code`   | Customer.area_code        | Same — exists in model, not in response |
| `is_verified` | Customer.is_verified      | Front may want to show verification status |
| `group`       | Customer.group.code       | Customer segment (B2B, VIP, etc.)       |

### Proposed v2 profile response

```jsonc
{
  "customer_id": "uuid",
  "firstname": "John",
  "lastname": "Doe",
  "email": "john@example.com",
  "nickname": null,                     // new — display name
  "avatar_url": null,                   // new — profile image URL
  "phone": "+48123456789",             // existing field, newly exposed
  "dialling_code": "+48",             // existing field, newly exposed
  "sex": "male",
  "language": "en",
  "is_verified": false,                // existing field, newly exposed
  "group": "b2b",                      // group.code or null
  "extra": {}
}
```

---

## 3. Price fields — checkout vs Matrix v2

This is the critical alignment. Front handles prices from both modules.

### Matrix v2 price object (established)

```
gross, net, final_gross, final_net,
special_gross, special_net, percent_off,
tax_rate ("0.23"), currency
```

Pattern: **always explicit gross/net in field name. Tax rate as decimal string.**

### Checkout v1 item price fields (current)

```
base_unit_price, base_total_price,
special_unit_price, special_total_price,
unit_price, total_price,
unit_price_netto, total_price_netto,
unit_tax_amount, total_tax_amount,
base_unit_tax_amount, base_total_tax_amount,
discount_amount, discount_amount_netto,
discount_percent, special_percent,
tax_rate (Decimal, e.g. 23)
```

Pattern: **no gross/net in base field names. `_netto` suffix added later as patch. Tax rate as integer.**

### Mapping checkout → Matrix v2 naming

| Checkout v1                  | What it is                    | Matrix v2 equivalent         | Proposed checkout v2          |
|------------------------------|-------------------------------|------------------------------|-------------------------------|
| `base_unit_price`            | Catalog price per unit (gross OR net) | `gross` | `unit_gross`                 |
| (missing)                    | Catalog price per unit net    | `net`                        | `unit_net`                    |
| `base_total_price`           | Catalog × qty                 | (computed)                   | `total_gross`                |
| (missing)                    | Catalog × qty net             | (computed)                   | `total_net`                   |
| `special_unit_price`         | Promo price per unit          | `special_gross`              | `special_unit_gross`         |
| (missing)                    | Promo price per unit net      | `special_net`                | `special_unit_net`            |
| `special_total_price`        | Promo × qty                   | (computed)                   | `special_total_gross`        |
| (missing)                    | Promo × qty net               | (computed)                   | `special_total_net`           |
| `unit_price`                 | Final unit price (ambiguous)  | `final_gross`                | `final_unit_gross`           |
| `unit_price_netto`           | Final unit price net          | `final_net`                  | `final_unit_net`             |
| `total_price`                | Final total (ambiguous)       | (computed)                   | `final_total_gross`          |
| `total_price_netto`          | Final total net               | (computed)                   | `final_total_net`            |
| `unit_tax_amount`            | Tax per unit                  | (not in Matrix)              | `unit_tax`                    |
| `total_tax_amount`           | Tax × qty                     | (not in Matrix)              | `total_tax`                   |
| `discount_amount`            | Discount gross                | (not in Matrix inline)       | `discount_gross`             |
| `discount_amount_netto`      | Discount net                  | (not in Matrix inline)       | `discount_net`               |
| `discount_percent`           | Discount %                    | `percent_off`                | `percent_off`                |
| `special_percent`            | Special price %               | `percent_off`                | `percent_off`                |
| `tax_rate` (Decimal 23)      | Tax rate                      | `tax_rate` ("0.23")          | `tax_rate` ("0.23")          |

### Naming principle: Matrix vs Checkout

Matrix price object is **per-product** (one SKU, one price).
Checkout price is **per-line-item** (SKU × quantity, with discounts applied).

Naming pattern:

| Context     | Matrix v2                | Checkout v2                  |
|-------------|--------------------------|------------------------------|
| Per unit    | `gross`, `net`           | `unit_gross`, `unit_net`     |
| Per line    | (not applicable)         | `total_gross`, `total_net`   |
| Final       | `final_gross`, `final_net`| `final_unit_gross`, `final_unit_net` |
| Final line  | (not applicable)         | `final_total_gross`, `final_total_net` |
| Special     | `special_gross`          | `special_unit_gross`         |
| Tax rate    | `"0.23"`                 | `"0.23"`                     |
| Discount %  | `percent_off`            | `percent_off`                |

**`unit_` prefix** distinguishes per-unit from per-line in checkout. Matrix doesn't need it (always per-product).

### Are there too many price fields?

Checkout v1 has ~20 price fields per item. v2 would have ~16 with explicit gross/net:

```
unit_gross, unit_net,                          // catalog price
total_gross, total_net,                        // catalog × qty
special_unit_gross, special_unit_net,          // promo price
special_total_gross, special_total_net,        // promo × qty
final_unit_gross, final_unit_net,              // after all discounts
final_total_gross, final_total_net,            // after all discounts × qty
unit_tax, total_tax,                           // tax amounts
discount_gross, discount_net                   // discount amounts
```

Plus `tax_rate`, `percent_off`, `currency`. That's ~19 fields.

**Can we reduce?** Yes — `total_*` fields are `unit_* × quantity`. Front can compute them.
Drop totals, keep only unit prices + quantity. Front multiplies:

```
unit_gross, unit_net,                          // catalog
special_unit_gross, special_unit_net,          // promo (null if no promo)
final_unit_gross, final_unit_net,              // after discounts
unit_tax,                                      // tax per unit
discount_gross, discount_net,                  // discount per unit
tax_rate, percent_off, currency, quantity      // meta
```

That's ~13 fields. Cleaner. But front has to multiply — same argument as percent_off
(we decided to compute server-side). **Decision needed: keep totals or let front compute?**

---

## 4. Cart-level totals — checkout vs Matrix

Matrix doesn't have cart-level data. But checkout's cart totals should follow same naming:

| Checkout v1          | Checkout v2 proposed     |
|----------------------|--------------------------|
| `base_total`         | `subtotal_gross`         |
| (missing)            | `subtotal_net`           |
| `total`              | `total_gross`            |
| (missing)            | `total_net`              |
| `total_tax`          | `total_tax`              |
| `fee_price`          | `fee_gross`              |
| (missing)            | `fee_net`                |
| `fee_tax_price`      | `fee_tax`                |
| `fee_tax_rate` (int) | `fee_tax_rate` ("0.23")  |

---

## Summary of alignment actions

| Action                           | Where                          | Priority     |
|----------------------------------|--------------------------------|--------------|
| Add `is_company` to checkout     | Checkout Address DTO           | High         |
| Standardize `tax_id` location    | Base address (both modules)    | Medium       |
| Expose phone/dialling_code in profile | Accounts profile response | Medium       |
| Add nickname, avatar_url fields  | Accounts Customer model        | Low (new feature) |
| Rename price fields (gross/net)  | Checkout item/shipping/cart DTOs | **Critical** |
| `tax_rate` int → decimal string  | Checkout all price contexts    | **Critical** |
| Add `_net` counterparts          | Checkout all price fields      | **Critical** |
| Monetary values → string format  | Checkout serialization         | High         |
| Canonical address in contract    | api-response-contract.md       | High         |
