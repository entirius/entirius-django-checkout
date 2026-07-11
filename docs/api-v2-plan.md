# Checkout API v2 — Plan Outline

Status: **DETAILED PLAN** — response shapes defined, error taxonomy complete, v1/v2 coexistence rules established

**Context:** Matrix v2 plan established conventions for price naming (gross/net always),
response contracts, and field naming. Checkout is the most impacted module — price fields
everywhere, and they don't match the new convention.

## Current state

v1 only. Function-based views, marshmallow DTOs, custom Response class.
Complex domain logic (pricing, tax, discounts, shipping, payment providers).
No Pydantic, no DRF ViewSets, no drf-spectacular.

## The big problem: price field naming

This is the #1 issue. Checkout and Matrix v2 speak different price languages.

### Matrix v2 price convention (established)

Explicit gross/net in every field name:
```
gross, net, final_gross, final_net,
special_gross, special_net,
range_from_gross, range_from_net, ...
tax_rate: "0.23"
```

### Checkout v1 price naming (current)

Context-dependent — same field name means different things based on channel config:
```
unit_price       → could be gross OR net (depends on discount_apply_type)
total_price      → could be gross OR net
base_unit_price  → always net (but not named _net)
unit_price_netto → explicitly net (only sometimes present)
tax_rate         → integer 23 (not string "0.23")
tax_amount       → Decimal (actual tax money amount)
```

### Mapping table

| Checkout v1 field                   | Matrix v2 equivalent          | Problem                                 |
|-------------------------------------|-------------------------------|-----------------------------------------|
| `unit_price`                        | `final_gross` or `final_net`  | Ambiguous — depends on channel config   |
| `total_price`                       | computed (qty × final)        | Same ambiguity                          |
| `base_unit_price`                   | `gross` (catalog price)       | Name doesn't say gross/net              |
| `special_unit_price`                | `special_gross`               | Same ambiguity                          |
| `unit_price_netto`                  | `final_net`                   | Explicit but inconsistent suffix        |
| `total_price_netto`                 | computed (qty × final_net)    | Same                                    |
| `unit_tax_amount`                   | computed from rate            | Not in Matrix price object              |
| `tax_rate` (int 23)                 | `tax_rate` ("0.23")           | Different format: int vs decimal string |
| `discount_amount`                   | (no direct equivalent)        | Matrix has percent_off, not amount      |
| `discount_amount_netto`             | (no direct equivalent)        | Checkout-specific                       |
| `fee_price`                         | (no Matrix equivalent)        | Payment method fee — checkout only      |

## Key inconsistencies vs Matrix v2 conventions

| Area                | Checkout v1                              | Matrix v2 contract                      | Impact |
|---------------------|------------------------------------------|-----------------------------------------|--------|
| Price naming        | Ambiguous (context-dependent)            | Explicit gross/net always               | **Critical** — core of the API |
| tax_rate format     | Integer `23`                             | Decimal string `"0.23"`                 | Breaking for front |
| Monetary values     | Decimal (Python objects)                 | String ("299.00")                       | Serialization change |
| Response wrapper    | Custom Response + `.add_regional()`      | Standard `{results: [...]}`             | Structural change |
| Error format        | Custom ErrorInfo                         | `{error, message, debug_id, details[]}` | Align |
| Null handling       | Mixed (`null`, `0`, `""`)                | Always `null` for absent                | Cleanup |
| Address fields      | `firstname`, `country_code`, `tax_id`    | Same as accounts — `firstname` kept as-is (Decision #1), `is_company` added (Decision #3) |
| Cart ID             | UUID — good                              | UUID — aligned                          | No change |
| Order ID            | UUID + pretty_id — good                  | No equivalent in Matrix                 | No conflict |

## v2 price strategy

Two options:

### Option A: Explicit gross/net everywhere (align with Matrix)

Cart item response becomes:
```json
{
  "sku": "CHAIR-001",
  "quantity": 2,
  "unit_gross": "349.00",
  "unit_net": "283.74",
  "final_unit_gross": "299.00",
  "final_unit_net": "243.09",
  "total_gross": "598.00",
  "total_net": "486.18",
  "tax_rate": "0.23",
  "discount_gross": "100.00",
  "discount_net": "81.30"
}
```

Pros: consistent with Matrix, unambiguous, no channel config guessing.
Cons: doubles field count, big migration.

### Option B: Keep ambiguous names, add `price_type` meta

```json
{
  "meta": {"price_type": "gross"},
  "items": [
    {"sku": "CHAIR-001", "unit_price": "349.00", "total_price": "698.00", ...}
  ]
}
```

Pros: minimal change. Cons: front still needs to interpret based on meta.

**Recommendation:** Option A. Consistency with Matrix outweighs migration effort.
Front code that handles both catalog prices (Matrix) and cart prices (checkout)
should see the same field names. `unit_gross` is `gross` from Matrix but per-unit.

## Scope for v2

| Priority   | Area              | What changes                                      |
|------------|-------------------|----------------------------------------------------|
| **Critical** | Price fields    | Explicit gross/net naming on all item/shipping/total fields |
| **Critical** | tax_rate format | `"0.23"` decimal string, not integer `23`          |
| High       | Cart response     | Standard response wrapper, Pydantic schemas        |
| High       | Address shape     | Canonical AddressSchema from response contract     |
| Medium     | Order response    | Align with cart price naming                       |
| Medium     | Error handling    | v2 error structure                                 |
| Low        | Payment webhooks  | Stay v1 (external providers, don't touch)          |
| Medium     | Shipping methods  | Full response spec (method_type, COD, free shipping, delivery point) |
| Medium     | Payment methods   | Full response spec (provider, extension data, fee) |
| Medium     | Order responses   | Create (redirect_url, splitting), list (filters), detail |

## Shared contracts (accounts + checkout + matrix)

Three things must be defined once in `api-response-contract.md`:

1. **Address shape** — used by accounts (CRUD) and checkout (cart/order)
2. **Price field naming** — used by Matrix (catalog) and checkout (cart/order)
3. **tax_rate format** — universal: `"0.23"` decimal string

## v1/v2 Coexistence Rule

**v1 stays running and untouched until all storefronts migrate.** This is non-negotiable.

### What this means in practice

1. **v1 views, URLs, response format — zero changes.** No renames, no format changes, no dropped fields.
   Every storefront currently calling v1 keeps working without any code change on their side.

2. **Both URL prefixes active simultaneously:**
   - v1: `/api/checkout/1/{channel}/...` (existing function-based views, marshmallow DTOs)
   - v2: `/api/checkout/v2/{channel}/...` (new DRF ViewSets, Pydantic schemas)

3. **Same domain logic underneath.** v1 views call `process_cart()`. v2 views call `process_cart()`.
   Same engine, different serialization layer on top. No fork of business logic.

4. **Same DB, same Cart model.** v1 and v2 read/write the same `cart_body` JSONField.
   A cart created via v1 can be read via v2 and vice versa. The JSON blob is the shared contract.

5. **v1 removal happens ONLY after:**
   - All storefronts migrated to v2
   - Monitoring confirms zero v1 traffic for 30+ days
   - Explicit decision to remove (not automatic)

### What CAN change in v1 scope

- **Bug fixes** in domain logic benefit both v1 and v2 (shared code)
- **New domain features** (e.g., new discount modifier) land in domain layer, accessible to both
- **DeliveryPoint cleanup** (see below): checkout's legacy DeliveryPoint model/views/table are removed,
  but this only affects `GET carts/<id>/delivery-points/` which is already unused by production storefronts.
  If any storefront still uses it, migrate them to `django-deliverypoints` API first.

### What CANNOT change in v1 scope

- Field names (no renames)
- Response structure (no flattening, no nesting changes)
- Error format (keeps `meta.errors[]` + `meta.messages[]`)
- Auth mechanism (`x-api-key` + existing decorators)
- URL patterns

## Domain logic stays untouched

v2 is an API layer change only. The domain logic in `domain/cart.py`, discount calculation,
tax rules, payment processing — all stays. Services wrap domain functions,
views serialize with Pydantic. Repository/domain layer is not rewritten.

## v2 Response Completeness Rule

**Principle: v2 ≥ v1.** Every v2 response MUST return at least as much data as v1, renamed
to v2 conventions. Front-end switching from v1→v2 must never lose information it previously had.

**How it works:**
1. Every v1 field gets one of three labels: **rename**, **keep**, or **drop**
2. `rename` = same data, new field name (e.g., `unit_price` → `final_unit_gross`)
3. `keep` = same name and format, or minor format change (e.g., `tax_rate` int→string)
4. `drop` = explicitly excluded — listed in the Drop List below with reason

**If a field is not in the Drop List, it MUST appear in v2.** No silent omissions.
During implementation, every Pydantic response schema must be cross-checked against
the corresponding v1 DTO. Missing fields that aren't on the Drop List = bug.

### Drop List (v1 fields deliberately excluded from v2)

| v1 field | DTO | Reason |
|----------|-----|--------|
| `name` (on cart items) | ValidatedItemData | Product display data comes from Matrix. Cart is transactional — SKU + prices only. Adding names would require PIM queries on every cart GET (Decision #1 in response shape) |
| `offer_price` (on cart items) | ItemData | Per-item price override. Unused by current storefronts. If needed later, add as opt-in field |
| `extra` (on cart items) | ItemData | Custom options dict for configurable products. Unused by current storefronts. If needed, add later |
| `sub_items` (on cart items) | ValidatedItemData | Bundle unpacking. Internal to domain, not needed in API response |
| `special_from_date`, `special_to_date` | ValidatedItemData | Sale validity windows. Front gets this from Matrix product data, not from cart |
| `base_unit_tax_amount`, `base_total_tax_amount` | ValidatedItemData | Pre-discount tax amounts. Redundant — front can compute from `unit_gross` × `tax_rate` if needed |
| `discount_percent` (item-level) | ValidatedItemData | Redundant — computable from `discount_gross` / `total_gross`. Confusable with `percent_off` (special price %) |
| `pk` (on shipping/payment methods) | Views | Internal DB ID. Not stable across environments |
| `GET carts/<id>/delivery-points/` | Endpoint | Replaced by `django-deliverypoints` public API. Checkout v1 model was FK-coupled to ShippingMethod, no channels, no geo, no T9N. New module is independent, multi-channel, has Haversine nearby search |

### Rename Map (quick reference)

Full rename maps per response type are in the respective sections below.
Summary of the pattern:

```
v1 ambiguous name     → v2 explicit name
unit_price            → final_unit_gross
total_price           → final_total_gross
base_unit_price       → unit_gross
unit_price_netto      → final_unit_net
discount_amount       → discount_gross
discount_amount_netto → discount_net
tax_rate (int 23)     → tax_rate (str "0.23")
currency_code         → currency
special_percent       → percent_off
```

## Decisions

| # | Topic | Decision | Status |
|---|-------|----------|--------|
| 1 | `firstname` naming | **Keep `firstname`** (no underscore). Both modules consistent, cart JSON in DB, payment providers. Not an exception — it's one word | Final |
| 2 | `address_id` in cart | **Keep `address_id` (Optional[int], passthrough) AND add `source: "address_book" / "manual"`.** `address_id` = front needs it to re-select address in UI. `source` = audit trail. Checkout never validates either — both are passthrough metadata stored in cart JSON | Final |
| 3 | `is_company` gap | **Add to checkout Address DTO.** Accounts has it, checkout doesn't. Canonical shape must include it | Final |
| 4 | Line item totals | **Server computes totals.** `total_gross = unit_gross × qty` etc. Front never multiplies. Cart has 3-10 lines, not 24 tiles — accuracy matters more than payload | Final |
| 5 | tax_rate format | **`"0.23"` decimal string everywhere.** v1 uses int `23` — breaking change, aligned with Matrix | Final |
| 6 | Monetary values | **Always string `"299.00"`.** v1 uses Python Decimal. v2 serializes to fixed-precision string — aligned with Matrix | Final |
| 7 | Payment webhooks | **Don't touch.** PayU, PayPal, Stripe etc. stay v1. External provider contracts, not our API | Final |
| 8 | Sub-resource PATCHes | **Split cart writes into typed sub-resources.** Each PATCH sends one concern, returns full cart. Inspired by commercetools update actions, Saleor separate mutations | Final |
| 9 | Full cart on every response | **Every write returns full cart state.** Cascading effects visible (e.g. address change resets shipping). Front always has current truth | Final |
| 10 | No separate validator | **Validation is per-step** (industry standard). Each PATCH validates its scope. `POST orders/` does final validation. No standalone validate endpoint | Final |

---

## v2 Cart Flow

Inspired by commercetools (typed update actions) and Saleor (separate mutations).

### Principle

**Small request, full response.** Each PATCH sends one concern (items, addresses, shipping, payment).
Backend processes that concern, recalculates cascading effects, returns full cart state.

### Dependency chain

```
items       → affects: shipping options (weight), gratis eligibility, totals
addresses   → affects: shipping options (country), payment options (country), tax rate
                        may RESET selected shipping/payment if no longer valid
shipping    → affects: totals (shipping cost)
payment     → affects: totals (payment fee)
discounts   → affects: item prices, totals
```

When a dependency changes, backend automatically recalculates downstream.
If selected shipping/payment becomes invalid after address change, it resets to `null`
in the response — front sees this and prompts user to re-select.

### Checkout flow (typical 6-8 calls)

```
1. POST   carts/                              create cart (items + currency)
2. PATCH  carts/<id>/items/                   add/remove/change quantities
3. PATCH  carts/<id>/discounts/               apply discount code (optional)
4. PATCH  carts/<id>/addresses/               set billing + shipping address
5. GET    carts/<id>/shipping-methods/         available methods (after address)
6. PATCH  carts/<id>/shipping/                 select shipping method
7. GET    carts/<id>/payment-methods/          available methods
8. PATCH  carts/<id>/payment/                  select payment method
9. POST   orders/                              finalize (final validation)
```

Each PATCH returns full cart — front always has current state.

### Endpoints

| # | Endpoint                              | Method | Request body                    | Response          |
|---|---------------------------------------|--------|---------------------------------|-------------------|
| 1 | `carts/`                              | POST   | `{items, currency, language}`   | Full cart         |
| 2 | `carts/latest/`                       | GET    | —                               | Full cart (auth)  |
| 3 | `carts/<id>/`                         | GET    | —                               | Full cart         |
| 4 | `carts/<id>/`                         | DELETE | —                               | 204               |
| 5 | `carts/<id>/items/`                   | PATCH  | `{items: [{sku, qty}]}`        | Full cart         |
| 6 | `carts/<id>/addresses/`               | PATCH  | `{billing: {...}, shipping: {...}}` | Full cart     |
| 7 | `carts/<id>/discounts/`               | PATCH  | `{codes: [...], clear}` (see Discount Deep Dive) | Full cart |
| 8 | `carts/<id>/shipping/`                | PATCH  | `{code, delivery_point?}`       | Full cart         |
| 9 | `carts/<id>/payment/`                 | PATCH  | `{code, bank_id?, card?, save_card?}` | Full cart   |
| 10| `carts/<id>/merge/`                   | POST   | `{guest_cart_id, operation: "add"\|"override"}` | Full cart |
| 11| `carts/<id>/shipping-methods/`        | GET    | —                               | See Shipping Methods Response |
| 12| `carts/<id>/payment-methods/`         | GET    | —                               | See Payment Methods Response |
| ~~13~~| ~~`carts/<id>/delivery-points/`~~ | ~~GET~~ | — | **REMOVED** — replaced by `django-deliverypoints` public API |
| 14| `carts/<id>/gratis-rules/`            | GET    | —                               | See Discount Deep Dive |
| 15| `orders/`                             | POST   | `{cart_id}`                     | See Order Creation Response |
| 16| `orders/`                             | GET    | `?status=&order_id=&sort=`      | See Order List Response |
| 17| `orders/<pretty_id>/`                 | GET    | —                               | Order detail      |
| 18| `orders/<pretty_id>/files/<file_id>/` | GET    | —                               | Binary file (Content-Disposition) |
| 19| `countries/`                          | GET    | `?language=en`                  | See Countries Response |

**Not in v2:** `notify/*` webhooks stay v1 (external provider contracts).

### Error scoping

Each PATCH validates only its concern:

| Endpoint           | Possible errors                                    |
|--------------------|----------------------------------------------------|
| `items/`           | `ITEM_NOT_SALEABLE`, `ITEM_OUT_OF_STOCK`, `ITEM_PRICE_CHANGED`, `ITEM_QTY_LIMITED`, `ITEM_QTY_CHANGED`, `ITEM_TOO_HEAVY`, `ITEM_NO_WEIGHT` |
| `addresses/`       | `ADDRESS_FIELD_REQUIRED`, `INVALID_EMAIL`, `COUNTRY_NOT_SUPPORTED`, `INVALID_VAT_NUMBER`, `VAT_COUNTRY_MISMATCH` |
| `discounts/`       | See "Discount Error Taxonomy" section (34 error codes) |
| `shipping/`        | `SHIPPING_NOT_AVAILABLE`, `SHIPPING_COUNTRY_MISMATCH`, `SHIPPING_RESET` (cascading from address change) |
| `payment/`         | `PAYMENT_NOT_AVAILABLE`, `PAYMENT_COUNTRY_MISMATCH`, `PAYMENT_RESET` (cascading) |
| `POST orders/`     | Any of the above (final gate) + `MIN_ORDER_NOT_MET`, `PAYMENT_REQUIRED`, `SHIPPING_REQUIRED`, `ADDRESSES_REQUIRED`, `CART_EMPTY` |

v1 mixed all these in one `meta.errors` array from one PATCH. v2 scopes them — front knows exactly what to fix.

### v1 fat PATCH still works

v1 `PATCH carts/<id>/` with full body stays unchanged (see "v1/v2 Coexistence Rule" above).
v2 sub-resource PATCHes are the preferred path for new storefronts. Both URL prefixes
coexist until all storefronts migrate and v1 traffic drops to zero.

### What does NOT change

- Domain logic (`domain/cart.py`, `process_cart()`) — same engine, different entry points
- Cart DB model (JSONField `cart_body`) — same storage
- Auth pattern (`x-api-key` + JWT) — same
- Cart ID in cookie — same
- Payment provider integration — same (webhooks stay v1)

### Frontend impact analysis

Based on Nuxt storefront `useCartCheckout.js` and `_flatters.js`:

**What front does today:**
1. Calls `ctma.POST._Cart(payload)` / `ctma.PATCH._Cart({cart_id, ...payload})`
2. Gets response with nested `{cart_id, total, base_total, total_tax, cart: {items: [...], discounts: [...]}, addresses: {...}, shipping_method: {...}}`
3. Runs `flatCart(data)` which adds `processed_` prefix to everything
4. Template reads `state.processed_total`, `state.processed_items`, etc.
5. For product data (name, image): separate call to Matrix `mtx_cma.GET._Products({sku: [...]})`

**What v2 eliminates:**
- `flatCart()` normalizer — unnecessary prefix indirection. v2 response is clean enough to use directly
- `flatCartGETS()` — same, just prefixing
- `flatOrder()` — two fields, no normalizer needed
- `flatCustoms()` — generic prefix, unnecessary

**What v2 changes in front:**
- 7 price field renames in template bindings (or one mapping object if front wants backward compat layer)
- `base_unit_price` → `unit_gross`
- `unit_price` → `final_unit_gross`
- `special_unit_price` → `special_unit_gross`
- `base_total_price` → `total_gross`
- `total_price` → `final_total_gross`
- `base_total` → `subtotal_gross`
- `total` → `total_gross` (cart level)
- New fields (additive, front ignores if not needed): `*_net` counterparts everywhere

**What v2 does NOT change:**
- Cart ID in cookie (`cid`) — same
- Auth pattern (`x-api-key` + `Authorization`) — same
- `$ctma` API instance structure — same, just `/v2/` in URL
- Checkout flow steps (address → shipping → payment → order) — same
- Product enrichment from Matrix (separate call) — same
- Discount code flow (apply, auto-remove invalid) — same
- Gratis/freebies flow — same
- Merge guest→customer — same

### Cart response v2 structure (proposed)

No `processed_` prefix, no flattening needed. Clean nested response.

```jsonc
{
  "cart_id": "uuid",
  "cart_status": "NEW",
  "validation_status": "valid",
  "currency": "EUR",                                    // was currency_code — aligned with Matrix

  // ── Cart totals ─────────────────────────────────────
  "subtotal_gross": "399.98",                            // was base_total
  "subtotal_net": "325.19",                              // new
  "total_gross": "419.97",                               // was total (incl shipping)
  "total_net": "341.44",                                 // new
  "total_tax": "78.53",                                  // unchanged
  "tax_rates": {"0.23": "78.53"},                        // tax breakdown by rate

  // ── Items ───────────────────────────────────────────
  "items": [
    {
      "sku": "ENT-S004",
      "quantity": 2,
      "status": "valid",
      "is_gratis": false,

      "unit_gross": "249.99",                            // catalog price per unit gross
      "unit_net": "203.24",                              // catalog price per unit net
      "total_gross": "499.98",                           // catalog × qty gross
      "total_net": "406.49",                             // catalog × qty net

      "has_special_price": true,                         // aligned with Matrix
      "special_unit_gross": "199.99",                    // promo price per unit gross
      "special_unit_net": "162.60",                      // promo price per unit net
      "special_total_gross": "399.98",                   // promo × qty gross
      "special_total_net": "325.19",                     // promo × qty net
      "percent_off": 20,                                 // special vs catalog %, aligned with Matrix

      "final_unit_gross": "199.99",                      // after all discounts per unit gross
      "final_unit_net": "162.60",                        // after all discounts per unit net
      "final_total_gross": "399.98",                     // after all discounts × qty gross
      "final_total_net": "325.19",                       // after all discounts × qty net

      "unit_tax": "37.39",                               // tax amount per unit
      "total_tax": "74.79",                              // tax amount × qty
      "tax_rate": "0.23",                                // decimal string, aligned with Matrix

      "discount_gross": "100.00",                        // code/rule discount amount gross
      "discount_net": "81.30"                            // code/rule discount amount net
    }
  ],

  // ── Addresses ───────────────────────────────────────
  "addresses": {
    "billing_address": {                                  // canonical shape from contract
      "firstname": "Jan",
      "lastname": "Kowalski",
      "email": "jan@example.com",
      "country_code": "PL",
      "city": "Warszawa",
      "postcode": "00-001",
      "street": "Marszałkowska 89",
      "dialling_code": "+48",
      "telephone": "500500500",
      "company": null,
      "is_company": false,                               // new — was missing in v1 checkout
      "tax_id": null,
      "vat_validation_status": null,                     // online VAT check result (billing only)
      "requested_invoice": false,
      "address_id": 42,                                  // passthrough — accounts address book PK (nullable)
      "source": "address_book"                           // "address_book" | "manual" — audit trail (nullable)
    },
    "shipping_address": null                              // null = same as billing
  },

  // ── Shipping (see "Shipping in Cart Response" for full spec) ──
  "shipping_method": {
    "code": "std_delivery",
    "name": "Standard Delivery",
    "method_type": "default",                               // default|inpost|pickup_point|delivery|virtual
    "status": "valid",
    "gross": "19.99",
    "net": "16.26",
    "modifier_gross": null,
    "modifier_net": null,
    "final_gross": "19.99",                                 // = "0.00" when free shipping
    "final_net": "16.26",
    "tax": "3.73",
    "tax_rate": "0.23",
    "cash_on_delivery_fee": null,
    "delivery_point": null                                  // or {code, name, address, city, postcode}
  },

  // ── Payment (see "Payment in Cart Response" for full spec) ──
  "payment_method": {
    "code": "payu",
    "name": "PayU",
    "provider": "payu",
    "status": "valid",
    "is_cash_on_delivery": false,
    "fee_gross": null,
    "fee_net": null,
    "fee_tax_rate": null,
    "bank_id": null,
    "card": null,
    "save_card": false,
    "pay_code": null
  },

  // ── Discount totals ─────────────────────────────────
  "total_discount_gross": "100.00",                     // sum of all item discount_gross
  "total_discount_net": "81.30",                        // sum of all item discount_net

  // ── Applied discounts ──────────────────────────────
  "discounts": [
    {
      "code": "SUMMER20",
      "modifier": "percent_discount",                   // discount type (see modifier enum)
      "value": 20,                                      // percent or fixed amount (from extra_value)
      "free_shipping": false,
      "free_order": false,
      "is_automatic": false,                            // server-discovered, not removable by user
      "status": "valid"                                 // "valid" or "invalid"
    },
    {
      "code": "BUY3_GET1",
      "modifier": "gratis_stepped",                     // gratis-type modifier
      "value": null,                                    // not applicable for gratis
      "free_shipping": false,
      "free_order": false,
      "is_automatic": false,
      "status": "valid",
      "gratis_sku": "CHAIR-001",                        // what user picked (gratis-type only)
      "gratis_quantity": 1                               // how many free units (gratis-type only)
    }
  ],

  // ── Payment fee (same value as payment_method.fee_*, duplicated at cart level for convenience) ──
  "fee_gross": null,                                     // included in total_gross, null if no fee
  "fee_net": null,
  "fee_tax": null,

  // ── Free shipping info ──────────────────────────────
  "free_shipping": false,
  "amount_missing_for_free_shipping": "80.03",
  "amount_required_for_free_shipping": "500.00",

  // ── Gratis info ─────────────────────────────────────
  // v1 had polymorphic available_gratis_rules (dict|list[str]|null) — v2 normalizes
  "gratis": {
    "is_available": true,
    "nearest_tier_price": "150.00",                     // was amount_required_for_nearest_gratis_rule
    "amount_missing": "50.00",                          // was amount_missing_for_nearest_gratis_rule (missing in v1 plan)
    "rules": [                                          // ALWAYS array (0, 1, or N) — no polymorphism
      {
        "code": "BUY3_GET1",
        "name": "Buy 3 get 1 free",
        "is_available": true,
        "max_available_quantity": 2,
        "all_tiers": {"200.00": 1, "500.00": 2}        // price threshold → free units
      }
    ]
  },

  // ── Min order ───────────────────────────────────────
  "min_order_price": "50.00",
  "amount_missing_for_min_order": "10.02",               // was total_to_min_order_price

  // ── Cart capabilities ──────────────────────────────
  "can_be_split": false,                                  // whether order will create multiple orders
  "allowed_guest_checkout": true,                         // front hides/shows login prompt
  "need_full_address": true                               // some channels allow partial address
}
```

### Key design decisions in response shape

1. **No `name` in cart items** — cart is transactional (SKU, qty, prices). Product display data (name, image, attributes) comes from Matrix via separate call (`build_Products()`). Adding names would require PIM/Matrix queries on every cart GET — wrong layer, kills performance.

2. **Flat items array, not nested cart.items** — v1 has `{cart: {items: [...], discounts: [...]}, total: ...}`. v2 flattens: `{items: [...], discounts: [...], total_gross: ...}`. No `cart` wrapper inside cart response — it IS the cart.

3. **`shipping_address: null`** means same as billing — v1 had separate fields always. v2: null = "copy billing". Frontend checkbox "different shipping address" controls this.

4. **Shipping method pricing** — simple `gross`/`net`/`tax`/`tax_rate`. Not the full 20-field Matrix price object — shipping has one price, no ranges, no special prices.

5. **`currency`** not `currency_code` — shorter, consistent with Matrix price object which uses `currency`.

6. **Gratis items are regular items with `is_gratis: true`** — gratis products appear in `items[]` like any other item. They have full pricing fields; `discount_gross` contains the near-full-price discount. Front uses `is_gratis` flag for display (e.g., "FREE" badge, hide remove button). Gratis selection stays in `PATCH discounts/` because a gratis IS a discount (modifier that adds an item instead of reducing price). Separating would require splitting `process_cart()` — not touching domain logic.

7. **Gratis info normalized** — v1 `available_gratis_rules` was polymorphic: 1 rule → dict, many → list of code strings, 0 → null. v2 normalizes to `gratis.rules[]` — always an array. Summary in cart response; full detail (eligible SKUs) via `GET gratis-rules/` endpoint.

8. **Discount totals at cart level** — v1 has `cart.discount_amount` + `cart.discount_amount_netto`. v2 maps to `total_discount_gross` / `total_discount_net`. Domain logic computes these — v2 just renames.

9. **Discount detail in response** — v1 returns full `ValidatedDiscountData` per discount (status, modifier, value, free_shipping, is_automatic). v2 keeps this — front needs it to show what the discount does and whether it can be removed. Gratis-type discounts additionally carry `gratis_sku` + `gratis_quantity` to show what user picked.

---

## Discount Sub-Resource — Deep Dive

v1 treats discounts as part of the fat cart PATCH. v2 extracts them to a dedicated sub-resource
while keeping the same domain logic underneath.

### Endpoint

```
PATCH carts/<id>/discounts/
```

### Request body

```jsonc
{
  "codes": [
    {"code": "SUMMER20"},
    {"code": "GRATIS_RULE", "sku": "CHAIR-001", "quantity": 1}  // gratis selection
  ],
  "clear": false            // true = remove all user-provided codes (automatic rules stay)
}
```

**Fields:**
- `codes[].code` — discount code string (required)
- `codes[].sku` — gratis product selection (optional, only for GRATIS_STEPPED modifier)
- `codes[].quantity` — gratis quantity (optional, only for GRATIS_STEPPED modifier)
- `clear` — `true` removes all manual codes; automatic rules are re-discovered regardless

**Remove a single code:** send `codes` without the code to remove. Server replaces stored codes
with what's in the request. Empty `codes: []` without `clear: true` also works.

**Gratis selection flow:**
1. Front calls `GET carts/<id>/gratis-rules/` → gets available tiers, eligible products
2. User picks product + quantity
3. Front sends `PATCH discounts/` with `sku` + `quantity` on the gratis code
4. Server validates eligibility, adds gratis item to cart, returns full cart

### Automatic discounts

Rules with `automatic_applications=True` are **always discovered and applied server-side**.
Front-end never sends them. They appear in the response with `"is_automatic": true`.

- `clear: true` does NOT remove automatic rules — only manual codes
- Front should render automatic discounts as non-removable (no X button)
- Automatic + manual can coexist if `combine_with_other_rules=True` on both

### Stacking / priority

- Discounts sorted by `priority` (lower = applied first), then `created_at`
- Highest-priority rule with `combine_with_other_rules=False` → only that rule applies
- `combine_with_other_rules=True` → stacks with others having same flag
- Each discount applied sequentially — later discounts see reduced totals from earlier ones

### Gratis rules endpoint

```
GET carts/<id>/gratis-rules/
```

Response:
```jsonc
[
  {
    "code": "BUY3_GET1",
    "name": "Buy 3 get 1 free",
    "is_available": true,
    "items": [                              // eligible products for selection
      {"sku": "CHAIR-001", "name": "..."},
      {"sku": "DESK-002", "name": "..."}
    ],
    "max_available_quantity": 2,
    "price_missing_to_next_tier": "50.00",  // amount needed for next free unit
    "next_gratis_tier_quantity": 3,
    "next_gratis_tier_price": "500.00",
    "all_tiers": {"200.00": 1, "500.00": 2, "1000.00": 3},
    "extension": {"banner_text": "Free gift!"}  // marketing data (optional)
  }
]
```

### What does NOT change vs v1

- Domain logic (`validate_discounts()`, `apply_discount_rule()`, `discount_worker.py`) — untouched
- DiscountRuleCode / DiscountCode / UsedCoupon models — untouched
- Stacking rules, priority sort, customer targeting — untouched
- Tax handling (BEFORE/AFTER based on `channel.discount_apply_type`) — untouched
- Gratis calculation engine — untouched

### Discount modifier enum (reference)

From `DiscountRuleCode.modifier` choices — for `discounts[].modifier` in response:

| Modifier | Category | Description |
|----------|----------|-------------|
| `none` | — | No discount (rule used for other flags) |
| `percent_discount` | Price | Simple percentage off |
| `price_discount` | Price | Fixed amount off |
| `step_qty_percent_discount` | Price | Tiered % based on single product qty |
| `step_qty_percent_discount_whole_cart` | Price | Tiered % based on total cart qty |
| `step_qty_price_discount_whole_cart` | Price | Tiered fixed amount based on total cart qty |
| `step_qty_fixed_price_per_currency` | Price | Tiered pricing per currency |
| `step_price_percent_discount` | Price | Tiered % based on cart total price |
| `cheapest_gratis` | Gratis | N free units of cheapest product |
| `most_expensive_gratis` | Gratis | N free units of most expensive product |
| `gratis_stepped` | Gratis | Customer picks which product gets free |
| `gratis_by_sku_in_cart` | Gratis | Free products when specific SKUs in cart |

Plus flags: `free_shipping` (bool), `free_order` (bool) — independent of modifier.

---

## Shipping Methods Response

`GET carts/<id>/shipping-methods/`

Available only after address is set (country determines available methods).

```jsonc
[
  {
    "code": "std_delivery",
    "name": "Standard Delivery",                      // translatable via ?language=
    "description": "2-3 business days",               // translatable
    "image": "https://cdn.../dhl.png",                // nullable
    "method_type": "default",                         // default|inpost|pickup_point|delivery|virtual
    "position": 1,                                    // sort order
    "country_code": "PL",
    "currency": "EUR",

    // ── Pricing ──
    "gross": "19.99",                                 // base shipping cost
    "net": "16.26",
    "modifier_gross": "5.00",                         // additional modifier surcharge (nullable)
    "modifier_net": "4.07",
    "final_gross": "24.99",                           // base + modifier (or "0.00" if free shipping)
    "final_net": "20.33",
    "tax_rate": "0.23",

    // ── Free shipping (computed per cart) ──
    "is_eligible_for_free_shipping": false,
    "amount_missing_for_free_shipping": "80.01",      // nullable — how much more to spend
    "amount_required_for_free_shipping": "500.00",    // nullable — threshold

    // ── COD ──
    "cash_on_delivery_available": true,
    "cash_on_delivery_fee": "5.00"                    // nullable
  }
]
```

Front uses `method_type` to decide UI:
- `default` / `delivery` → standard address delivery
- `inpost` / `pickup_point` → show `GET delivery-points/` picker after selection
- `virtual` → no physical delivery (digital goods)

---

## Payment Methods Response

`GET carts/<id>/payment-methods/`

```jsonc
[
  {
    "code": "payu",
    "provider": "payu",                               // payu|paypal|stripe|autopay|przelewy24|paynow|cod|free_order|payu_blik|transfer
    "name": "PayU",                                   // translatable
    "description": "Secure online payment",           // translatable, nullable
    "image": "https://cdn.../payu.png",               // nullable
    "position": 1,                                    // sort order
    "is_cash_on_delivery": false,
    "is_pay_code": false,                             // true if provider generates a pay code (e.g., Przelewy24)

    // ── Fee ──
    "fee_display": "2.5 %",                          // formatted for display (or "2.50 EUR")
    "fee_gross": "2.50",                              // computed for current cart total, nullable
    "fee_net": "2.03",
    "fee_tax_rate": "0.23",

    // ── Provider-specific extension ──
    "extension": null                                 // e.g., {"banks": [...]} for Przelewy24, null for most
  }
]
```

---

## Shipping in Cart Response (expanded)

When a shipping method is selected, the `shipping_method` object in cart response:

```jsonc
"shipping_method": {
  "code": "std_delivery",
  "name": "Standard Delivery",
  "method_type": "default",
  "status": "valid",                                  // valid|invalid (invalid if country changed)

  // ── Pricing ──
  "gross": "19.99",                                   // base cost
  "net": "16.26",
  "modifier_gross": "5.00",                           // surcharge, nullable
  "modifier_net": "4.07",
  "final_gross": "19.99",                             // after free delivery threshold (= "0.00" when free)
  "final_net": "16.26",
  "tax": "3.73",
  "tax_rate": "0.23",

  // ── COD ──
  "cash_on_delivery_fee": null,                       // only if COD payment selected

  // ── Delivery point (only for inpost/pickup_point) ──
  "delivery_point": null                              // or {"code": "WAW123", "name": "...", "address": "...", "city": "...", "postcode": "..."}
}
```

---

## Payment in Cart Response (expanded)

When a payment method is selected, the `payment_method` object in cart response.
This is a **state machine**, not just pricing — it carries provider interaction state.

```jsonc
"payment_method": {
  "code": "payu",
  "name": "PayU",
  "provider": "payu",
  "status": "valid",                                  // valid|invalid
  "is_cash_on_delivery": false,

  // ── Fee ──
  "fee_gross": "2.50",                                // nullable — no fee for most methods
  "fee_net": "2.03",
  "fee_tax_rate": "0.23",

  // ── Provider state (set during checkout, null until interaction) ──
  "bank_id": null,                                    // Przelewy24: selected bank
  "card": null,                                       // card token (if saved card)
  "save_card": false,                                 // user opted to save card
  "pay_code": null,                                   // generated by provider
  "authorization_token": null,                        // from provider (internal, may omit from public API)
  "continue_url": null                                // post-payment redirect (internal, may omit)
}
```

**Note:** `authorization_token` and `continue_url` are internal provider state — decide
whether to expose in public v2 or keep server-side only. v1 includes them in cart body
because it's stored as JSON blob. v2 could separate cart-for-frontend from cart-in-DB.

---

## Cart-Level Fields (expanded)

Fields present in v1 `CartResponse` / `CheckoutData` that were missing from the proposed v2 response:

```jsonc
{
  // ... existing fields (items, addresses, shipping, payment, discounts, gratis) ...

  // ── Fee totals (from payment method) ──────────
  "fee_gross": "2.50",                                // payment method fee, included in total_gross
  "fee_net": "2.03",
  "fee_tax": "0.47",

  // ── Free shipping info ────────────────────────
  "free_shipping": false,
  "amount_missing_for_free_shipping": "80.03",
  "amount_required_for_free_shipping": "500.00",

  // ── Cart capabilities ─────────────────────────
  "can_be_split": false,                              // whether order will create multiple orders
  "allowed_guest_checkout": true,                     // front hides/shows login prompt
  "need_full_address": true,                          // some channels allow partial address

  // ── Min order ─────────────────────────────────
  "min_order_price": "50.00",
  "amount_missing_for_min_order": "10.02"             // was total_to_min_order_price — how much more needed
}
```

---

## Item Statuses (v2)

v1 has 6 statuses. v2 keeps all — front needs CHANGED_* to display smart warnings.

| Status | Meaning | Frontend action |
|--------|---------|-----------------|
| `valid` | Item OK | Normal display |
| `invalid` | Unrecoverable (product removed from catalog) | Show error, suggest removal |
| `out_of_stock` | Zero stock | Show "out of stock", suggest removal |
| `changed_price` | Price changed since cart was created | Show old/new price, ask to accept |
| `changed_qty` | Requested qty > available stock, auto-reduced | Show "reduced to {qty}", explain |
| `changed_price_and_qty` | Both price and qty changed | Show both warnings |

---

## Order Creation Response

`POST orders/` — finalizes cart into order(s).

```jsonc
// 201 Created
{
  "order_id": "uuid",
  "order_pretty_id": "0100001",                       // human-readable ID
  "order_status": "UNPAID",                           // or "CONFIRMED" for COD/free
  "redirect_url": "https://payu.com/pay/abc123",      // payment provider URL — null for COD/free
  "split_orders_pretty_ids": []                        // if order was split: ["0100001", "0100002"]
}
```

**Order splitting:** When cart items match split rules (e.g., different suppliers), one cart
creates multiple orders. `split_orders_pretty_ids` lists all created order IDs.
`can_be_split` in cart response warns the front beforehand.

**Redirect flow:**
- PayU/PayPal/Stripe/etc. → `redirect_url` points to payment gateway, front redirects
- COD / FREE_ORDER → `redirect_url: null`, `order_status: "CONFIRMED"`, front shows success

---

## Order List Response

`GET orders/` — authenticated, returns customer's orders.

**Filters:**
- `?order_id=` — partial match on pretty_id or UUID
- `?status=` — exact match (UNPAID, CONFIRMED, SHIPPED, etc.)
- `?sort=created:desc` — sort field + direction (default: created desc)

```jsonc
{
  "count": 42,
  "next": "?page=2",
  "previous": null,
  "results": [
    {
      "order_id": "uuid",
      "pretty_id": "0100001",
      "status": "CONFIRMED",
      "status_label": "Potwierdzone",                 // translated to customer language
      "created": "2025-03-15T14:30:00Z",
      "updated": "2025-03-16T09:00:00Z",

      // ── Totals (same naming as cart) ──
      "total_gross": "419.97",
      "total_net": "341.44",
      "currency": "EUR",

      // ── Summary (for list display) ──
      "item_count": 3,
      "shipping_method_code": "std_delivery",
      "payment_method_code": "payu",

      // ── Attachments ──
      "attachments": [
        {"file_id": 1, "name": "invoice_0100001.pdf"}
      ]
    }
  ]
}
```

**Note:** v1 returns full `order_body` (entire cart snapshot) per order in the list. v2 should
return a slim summary in list, full body only in detail. Less payload, faster list rendering.

---

## Order Detail Response

`GET orders/<pretty_id>/` — full order with cart snapshot.

Same as list item plus full `order_body` — the complete cart state at time of order:
items (with prices), addresses, shipping, payment, discounts, totals.
Follows same naming as cart response (gross/net, string amounts, decimal tax_rate).

Additionally:
- `shipping_intent` — tracking info if available
- `invoices` — list of generated invoices
- `attachments` — downloadable files

---

## Delivery Points — Delegated to django-deliverypoints

**v1 checkout had `GET carts/<id>/delivery-points/`** — tightly coupled to ShippingMethod FK,
single-language, no geo search. This is replaced entirely by `django-deliverypoints` public API.

**v2 checkout does NOT have a delivery points endpoint.** Storefront calls deliverypoints directly:

```
GET /api/deliverypoints/v2/{channel}/points/?search=&type=inpost&language=pl
GET /api/deliverypoints/v2/{channel}/points/nearby/?lat=50.1&lon=19.9&radius_km=10
GET /api/deliverypoints/v2/{channel}/types/
```

**Checkout's only interaction with delivery points:**
1. Front selects a point from `django-deliverypoints` API
2. Front sends point code in shipping PATCH: `{"code": "inpost", "delivery_point": {"code": "WAW123"}}`
3. Checkout stores `delivery_point.code` in cart body JSON — no FK, no validation against deliverypoints DB
4. Cart response echoes back the stored delivery point data in `shipping_method.delivery_point`

**Checkout's legacy `DeliveryPoint` model** (FK to ShippingMethod) is scheduled for removal
(see cleanup section below). Before removing, confirm no production storefront calls
`GET carts/<id>/delivery-points/` — if any do, migrate them to `django-deliverypoints` API first.
Different concepts:
- Checkout's model = shipping method configuration (which methods support which point types)
- django-deliverypoints = public delivery point catalog (locations, geo, translations)

**Drop List addition:** checkout v2 Drop List includes the `GET carts/<id>/delivery-points/` endpoint.

### Cleanup: remove legacy DeliveryPoint from checkout

Checkout's `DeliveryPoint` model, views, manager, admin, management command, and DTO are dead weight.
They create confusion — two `DeliveryPoint` classes in the same Django project, different tables,
different schemas. Must be removed as part of v2 migration.

**Files to remove/clean (12 files touched):**

| File | Action |
|------|--------|
| `models/delivery_point.py` | **DELETE** — model + manager (28 lines) |
| `models/__init__.py` | Remove `DeliveryPoint` import/export |
| `models/cart.py` | Remove `available_delivery_points` property (~15 lines) |
| `views/delivery_point.py` | **DELETE** — endpoint view |
| `urls.py` | Remove `delivery-points/` URL pattern |
| `domain/dto/delivery_point.py` | **DELETE** — DTO (name, address, postcode, code, city) |
| `domain/dto/shipping.py` | Change `delivery_point` field from DTO object to plain `dict | None` (just stores what front sends) |
| `domain/validators/shipping.py` | Remove any DeliveryPoint queryset usage |
| `filters.py` | Remove DeliveryPoint filter class |
| `admin.py` | Remove DeliveryPoint admin registration |
| `management/commands/import-inpost-dp-from-dpm-csv.py` | **DELETE** — replaced by `django-deliverypoints` import command |
| `migrations/0001_initial.py` | Leave as-is (migration history). Add new migration to drop `checkout_deliverypoint` table |

**Migration:** Create `0003_remove_deliverypoint.py` (or next number) that drops the table.
Run after confirming no production code reads `checkout_deliverypoint` table.

**Timing:** Do this cleanup BEFORE implementing v2 endpoints — otherwise Pydantic schemas
might accidentally reference the wrong DeliveryPoint.

---

## Countries Response

`GET countries/?language=en`

```jsonc
{
  "countries": [
    {"code": "PL", "label": "Poland", "prefix": "+48"},
    {"code": "DE", "label": "Germany", "prefix": "+49"}
  ],
  "default_country": {"code": "PL", "label": "Poland", "prefix": "+48"}
}
```

`label` is language-aware (EN/PL based on `?language=` param, fallback to channel default).
`prefix` = phone dialling code. `default_country` always included even if not in channel's list.

---

## Merge Endpoint Detail

`POST carts/<id>/merge/`

Merges guest cart into authenticated customer's cart. Requires JWT auth.

```jsonc
// Request
{
  "guest_cart_id": "uuid",
  "operation": "add"                                  // "add" | "override"
}
```

- **`add`** — merge items by SKU, sum quantities (guest + customer)
- **`override`** — replace customer's items with guest's items

Guest cart is deleted after merge. Discounts are recomputed on the merged cart
(not carried over — `process_cart()` re-validates from scratch).

Returns full cart response.

---

## v2 Admin Endpoints (CMS panel — new)

URL prefix: `api/checkout/v2/admin/<channel_idx>/`

Currently only `customer/delete` exists. Admin CRUD is needed for CMS panel (Hugin).

### Phase 1 — Order management

| # | Endpoint                              | Method        | What it does                              |
|---|---------------------------------------|---------------|-------------------------------------------|
| 1 | `orders/`                             | GET           | List orders (filters: status, date, email, customer) |
| 2 | `orders/<uid>/`                       | GET           | Order detail (full body + payment + shipping intents) |
| 3 | `orders/<uid>/status/`                | PATCH         | Update order status (with validation: allowed transitions) |
| 4 | `orders/<uid>/cancel/`                | POST          | Cancel order (stock release, payment cancel if possible) |
| 5 | `orders/<uid>/attachments/`           | GET,POST      | List / upload order attachments (invoices) |

### Phase 2 — Configuration management

| # | Endpoint                              | Method          | What it does                            |
|---|---------------------------------------|-----------------|-----------------------------------------|
| 6 | `shipping-methods/`                   | GET,POST        | List / create shipping methods          |
| 7 | `shipping-methods/<code>/`            | GET,PATCH,DELETE | Detail / update / deactivate            |
| 8 | `payment-methods/`                    | GET,POST        | List / create payment methods           |
| 9 | `payment-methods/<code>/`             | GET,PATCH,DELETE | Detail / update / deactivate            |
| 10| `discount-codes/`                     | GET,POST        | List / create discount codes            |
| 11| `discount-codes/<code>/`              | GET,PATCH,DELETE | Detail / update / deactivate            |

### Phase 3 — Stock & reporting

| # | Endpoint                              | Method          | What it does                            |
|---|---------------------------------------|-----------------|-----------------------------------------|
| 12| `stock/`                              | GET             | Stock levels per product per supplier   |
| 13| `stock/<sku>/`                        | PATCH           | Update stock quantity                   |
| 14| `channels/<idx>/`                     | GET,PATCH       | Channel checkout config (min order, discount mode, etc.) |

### Admin auth

JWT + `IsAdminUser` — same pattern as PIM admin v2.
Replaces v1 `APIAdminKey` header auth (SHA256 key per channel).

---

---

## Error Handling Architecture

### v1 problems

1. **Mixed error types** — `process_cart()` collects a `messages[]` list containing both plain strings
   and `ErrorInfo` objects. Views have to `isinstance()` check each item. Front gets errors in two
   places: `meta.errors[]` and `meta.messages[]`.

2. **Last-error-wins in OrderValidation** — `flag = True; message = "..."` pattern overwrites previous
   errors. If payment AND shipping are missing, front only sees shipping.

3. **No field-level granularity** — `ErrorInfo` has `affected_field` but most errors don't use it.
   Front can't highlight which form field is wrong.

4. **Item status separate from errors** — items have `status: "out_of_stock"` but this isn't in
   `meta.errors[]`. Front has to check both `meta.errors` AND iterate `items[].status`.

### v2 error format

Single structured format. Aligns with v2 error contract (`{error, message, debug_id, details[]}`),
extended with `scope`, `field`, and `meta` for checkout-specific granularity.

```jsonc
{
  "error": "VALIDATION_ERROR",
  "message": "Cart validation failed.",
  "debug_id": "f7a2c3b8",
  "details": [
    {
      "scope": "items",                           // which sub-resource
      "field": "items[0].quantity",               // JSON path — front highlights field
      "code": "ITEM_OUT_OF_STOCK",               // machine-readable
      "message": "Insufficient stock for SKU ENT-S004. Available: 3, requested: 5.",
      "meta": {                                   // optional context for smart recovery
        "sku": "ENT-S004",
        "available_quantity": 3,
        "requested_quantity": 5
      }
    },
    {
      "scope": "addresses",
      "field": "billing_address.email",
      "code": "INVALID_EMAIL",
      "message": "Email address contains invalid characters.",
      "meta": {"value": "not-an-email"}
    }
  ]
}
```

### Error scopes

`scope` maps 1:1 to sub-resource endpoints:

| scope        | Endpoint              | Error codes                                              |
|--------------|-----------------------|----------------------------------------------------------|
| `items`      | `PATCH items/`        | ITEM_NOT_SALEABLE, ITEM_OUT_OF_STOCK, ITEM_PRICE_CHANGED, ITEM_QTY_LIMITED |
| `addresses`  | `PATCH addresses/`    | INVALID_EMAIL, COUNTRY_NOT_SUPPORTED, ADDRESS_FIELD_REQUIRED |
| `discounts`  | `PATCH discounts/`    | See "Discount Error Taxonomy" section below |
| `shipping`   | `PATCH shipping/`     | SHIPPING_NOT_AVAILABLE, SHIPPING_COUNTRY_MISMATCH         |
| `payment`    | `PATCH payment/`      | PAYMENT_NOT_AVAILABLE, PAYMENT_COUNTRY_MISMATCH           |
| `cart`       | `POST orders/` (gate) | MIN_ORDER_NOT_MET, PAYMENT_REQUIRED, SHIPPING_REQUIRED, ADDRESSES_REQUIRED |

### Per-step validation

Each sub-resource PATCH validates only its scope:
- `PATCH items/` → returns only `scope: "items"` errors
- `PATCH addresses/` → returns `scope: "addresses"` errors + cascading `scope: "shipping"` if method reset

### Cascading errors

When a change invalidates downstream state, response includes errors from affected scopes:

```jsonc
// PATCH addresses/ — changed country from PL to DE
{
  "error": "VALIDATION_WARNING",
  "message": "Address updated. Shipping method reset — no longer available for new country.",
  "details": [
    {
      "scope": "shipping",
      "field": "shipping_method",
      "code": "SHIPPING_RESET",
      "message": "Shipping method 'inpost' is not available for country DE. Please re-select.",
      "meta": {"previous_code": "inpost", "country": "DE"}
    }
  ]
}
```

Cart response still has full state — `shipping_method: null` tells front to show shipping selection again.

### Final gate (POST orders/)

Validates everything. Can return errors from any scope:

```jsonc
{
  "error": "VALIDATION_ERROR",
  "message": "Cannot create order.",
  "details": [
    {"scope": "cart",  "code": "MIN_ORDER_NOT_MET", "message": "Minimum order is 50.00 EUR. Current: 39.98 EUR.", "meta": {"min": "50.00", "current": "39.98"}},
    {"scope": "items", "code": "ITEM_OUT_OF_STOCK", "field": "items[2].quantity", "message": "...", "meta": {"sku": "ENT-X001"}},
    {"scope": "cart",  "code": "PAYMENT_REQUIRED",  "message": "Payment method not selected."}
  ]
}
```

### `meta` for smart frontend recovery

`meta` is optional context that enables frontend to offer fixes instead of just showing errors:

| Code | meta fields | Frontend action |
|------|-------------|-----------------|
| `ITEM_OUT_OF_STOCK` | `available_quantity` | "Change to {available}?" button |
| `ITEM_PRICE_CHANGED` | `old_price`, `new_price` | "Price changed from X to Y. Continue?" |
| `ITEM_QTY_LIMITED` | `max_quantity` | Auto-reduce to max |
| `DISCOUNT_EXPIRED` | `expired_at` | "Code expired on {date}" |
| `MIN_ORDER_NOT_MET` | `min`, `current` | "Add {diff} more to checkout" |
| `SHIPPING_RESET` | `previous_code`, `country` | Show shipping selection step |

### Implementation: v1 domain stays, v2 maps errors

Domain logic (`process_cart()`, validators) keeps returning `messages[]` with mixed types.
New error mapper layer converts to v2 format:

```python
# services/error_mapper.py

def map_to_v2_details(messages: list) -> list[dict]:
    """Convert v1 messages/ErrorInfo mix to v2 details array."""
    details = []
    for msg in messages:
        if isinstance(msg, ErrorInfo):
            details.append({
                "scope": infer_scope(msg.code, msg.affected_field),
                "field": msg.affected_field,
                "code": normalize_code(msg.code),
                "message": msg.message,
                "meta": build_meta(msg),
            })
        elif isinstance(msg, str):
            details.append({
                "scope": "cart",
                "field": None,
                "code": "GENERAL_WARNING",
                "message": msg,
                "meta": None,
            })
    return details
```

v1 views read `messages` as before. v2 views call `map_to_v2_details(messages)`.
Incremental: each new error code gets a proper mapping. Old strings become `GENERAL_WARNING` until migrated.

### Item status → error unification

v1 items have `status` field separate from global errors. v2 unifies:

```python
# In error mapper, after process_cart:
for i, item in enumerate(cart_data.items):
    if item.status == ItemStatus.OUT_OF_STOCK:
        details.append({
            "scope": "items",
            "field": f"items[{i}].quantity",
            "code": "ITEM_OUT_OF_STOCK",
            "message": f"SKU {item.sku} is out of stock.",
            "meta": {"sku": item.sku},
        })
    elif item.status == ItemStatus.CHANGED_PRICE:
        details.append({
            "scope": "items",
            "field": f"items[{i}].unit_gross",
            "code": "ITEM_PRICE_CHANGED",
            "message": f"Price changed for SKU {item.sku}.",
            "meta": {"sku": item.sku},
        })
```

Items still carry `status` field in cart response (backward compat). But errors are also in `details[]` — one place to check.

---

## Discount Error Taxonomy

Complete catalog of every discount failure path in v1 domain logic, mapped to v2 error codes.
Source files: `validators/discounts.py`, `worker/discount_worker.py`, `validators/cart.py`.

### Phase 1: Code Lookup & Activation (validators/discounts.py)

These checks happen in `validate_discounts()` before any calculation.

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 1 | Code string not found in `DiscountCode` table | `DISCOUNT_CODE_NOT_FOUND` | discounts | `codes[{i}].code` | `{code}` | L193: `codes__code__in=discount_codes` filter yields no match → falls to `factor_invalid_discount()` L332 |
| 2 | `DiscountRuleCode.is_active=False` | `DISCOUNT_RULE_INACTIVE` | discounts | `codes[{i}].code` | `{code}` | L172: `is_turn_on = Q(is_active=True)` |
| 3 | `DiscountCode.active_from` in the future | `DISCOUNT_NOT_YET_ACTIVE` | discounts | `codes[{i}].code` | `{code, active_from}` | L170: `codes__active_from__lte=current_date` |
| 4 | `DiscountCode.active_to` in the past | `DISCOUNT_EXPIRED` | discounts | `codes[{i}].code` | `{code, expired_at}` | L171: `codes__active_to__gte=current_date` |
| 5 | `DiscountCode.current_used >= max_used` (global limit) | `DISCOUNT_USAGE_LIMIT_REACHED` | discounts | `codes[{i}].code` | `{code, max_used, current_used}` | L238: `current_used__lt=F("max_used")` |
| 6 | `max_uses_per_user` exceeded for this email | `DISCOUNT_PER_USER_LIMIT_REACHED` | discounts | `codes[{i}].code` | `{code, max_uses_per_user, user_usage_count}` | L276-281: `have_previous_order_with_discount_code()` check |

### Phase 2: Rule Eligibility (validators/discounts.py)

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 7 | Rule not assigned to this channel | `DISCOUNT_WRONG_CHANNEL` | discounts | `codes[{i}].code` | `{code, channel_idx}` | L188-189: `base_query.filter(channels=channel)` |
| 8 | `min_order_amount` not met | `DISCOUNT_MIN_ORDER_NOT_MET` | discounts | `codes[{i}].code` | `{code, min_order_amount, current_total}` | L186: `min_order_amount__lte=total_price` |
| 9 | `target=FIRST_ORDER_LOGGED` but user not logged in | `DISCOUNT_LOGIN_REQUIRED` | discounts | `codes[{i}].code` | `{code, target: "first_order_logged"}` | L203-207: `is_logged=True` check |
| 10 | `target=FIRST_ORDER_*` but user has previous orders | `DISCOUNT_FIRST_ORDER_ONLY` | discounts | `codes[{i}].code` | `{code, target}` | L202: `have_previous_order_by_email=False` |
| 11 | Customer excluded by `DiscountCustomerModeOfAction` (group/customer filter) | `DISCOUNT_CUSTOMER_NOT_ELIGIBLE` | discounts | `codes[{i}].code` | `{code}` | L214: `customer_modifier_filter()` |
| 12 | `max_products_qty` threshold: highest item qty in cart < limit | `DISCOUNT_QTY_THRESHOLD_NOT_MET` | discounts | `codes[{i}].code` | `{code, required_qty, highest_qty}` | L177: `codes__max_products_qty__gt=highest_quantity` |
| 13 | Currency not supported by `extra_value` dict | `DISCOUNT_CURRENCY_NOT_SUPPORTED` | discounts | `codes[{i}].code` | `{code, currency, supported_currencies}` | L312,336: `is_currency_supported_by_discount()` |
| 14 | `allow_for_discount_codes=False` (PriceTuner blocks discounts) | `DISCOUNT_BLOCKED_BY_PRICE_RULES` | discounts | — | `{reason: "price_tuner"}` | L147,513-517: ErrorInfo `price_tuner_no_discount_codes` |

### Phase 3: Stacking & Priority (validators/discounts.py)

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 15 | Higher-priority rule has `combine_with_other_rules=False` → this code blocked | `DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE` | discounts | `codes[{i}].code` | `{code, blocking_code, blocking_priority}` | L268-274: priority filtering, L306-308: `rules_calc_pk` duplicate check |
| 16 | Same rule already applied (duplicate code for same rule PK) | `DISCOUNT_DUPLICATE_RULE` | discounts | `codes[{i}].code` | `{code}` | L306: `dc.pk in rules_calc_pk` |

### Phase 4: Product Filter (worker/discount_worker.py)

These happen in `apply_discount_rule()` → `filter_by_inclusion_and_exclusion()`.

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 17 | No cart products match inclusion filter (SKU/category/attribute/feature) | `DISCOUNT_NO_ELIGIBLE_PRODUCTS` | discounts | `codes[{i}].code` | `{code, modifier}` | L144: `if not filtered_skus.exists()` → `status = INVALID` |
| 18 | Cart total outside `cart_price_from/to` range in ModeOfAction | `DISCOUNT_CART_PRICE_OUT_OF_RANGE` | discounts | `codes[{i}].code` | `{code, cart_price_from, cart_price_to, current_total}` | L376-382: `filter_cart_by_numeric_value()` |
| 19 | Cart qty outside `cart_qty_from/to` range in ModeOfAction | `DISCOUNT_CART_QTY_OUT_OF_RANGE` | discounts | `codes[{i}].code` | `{code, cart_qty_from, cart_qty_to, current_qty}` | L358-364: `filter_cart_by_specify_value()` |
| 20 | Individual product price outside `product_price_from/to` range | `DISCOUNT_PRODUCT_PRICE_OUT_OF_RANGE` | discounts | `codes[{i}].code` | `{code, sku}` | L334-342: `filter_items_by_numeric_value()` |
| 21 | Individual product qty outside `qty_from/to` range | `DISCOUNT_PRODUCT_QTY_OUT_OF_RANGE` | discounts | `codes[{i}].code` | `{code, sku}` | L346-352: per-item qty filter |

### Phase 5: Gratis-Specific (worker/discount_worker.py)

These happen in `calculate_and_valid_gratis()`.

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 22 | `GRATIS_BY_SKU_IN_CART`: required SKUs not all present in cart | `GRATIS_REQUIRED_SKUS_MISSING` | discounts | `codes[{i}].code` | `{code, required_skus, present_skus, sku_logic}` | L618-624: `check_is_gratis_allowed()` — AND/OR logic on `extra_value["sku"]` |
| 23 | Picked SKU not in allowed products (`skus_allowed_to_gratis`) | `GRATIS_SKU_NOT_ALLOWED` | discounts | `codes[{i}].sku` | `{code, picked_sku, allowed_skus}` | L1026,1034: `picked_sku in skus_allowed_to_gratis` |
| 24 | Picked quantity exceeds allowed (GRATIS_BY_SKU: `extra_value["quantity"]`) | `GRATIS_QTY_EXCEEDED` | discounts | `codes[{i}].quantity` | `{code, picked_qty, max_qty}` | L1028: `picked_quantity <= extra_value.get("quantity", 1)` |
| 25 | Picked quantity ≤ 0 | `GRATIS_QTY_INVALID` | discounts | `codes[{i}].quantity` | `{code, picked_qty}` | L1028,1048: `0 < picked_quantity` |
| 26 | `GRATIS_STEPPED`: cart total below minimum tier threshold | `GRATIS_TIER_NOT_REACHED` | discounts | `codes[{i}].code` | `{code, min_tier_price, current_total}` | L1044: `total_price < min(thresholds.keys())` |
| 27 | `GRATIS_STEPPED`: picked quantity exceeds closest threshold allowance | `GRATIS_TIER_QTY_EXCEEDED` | discounts | `codes[{i}].quantity` | `{code, picked_qty, max_for_tier}` | L1048: `picked_quantity <= closest_threshold` |
| 28 | `GRATIS_STEPPED`: no thresholds found for currency | `GRATIS_NO_THRESHOLDS_FOR_CURRENCY` | discounts | `codes[{i}].code` | `{code, currency}` | L1037: `if not thresholds` |
| 29 | No allowed gratis products after filter_by_inclusion_and_exclusion | `GRATIS_NO_ELIGIBLE_PRODUCTS` | discounts | `codes[{i}].code` | `{code}` | L1016-1017: `products_allowed_to_gratis.exists()` |

### Phase 6: Calculation Edge Cases (worker/discount_worker.py)

| # | v1 check | v2 error code | scope | field | meta | v1 source |
|---|----------|---------------|-------|-------|------|-----------|
| 30 | `extra_value` invalid format (TypeError in calculation) | `DISCOUNT_CONFIG_ERROR` | discounts | `codes[{i}].code` | `{code, rule_id}` | L644: `except TypeError: raise Exception(f"...extra_value...")` |
| 31 | `get_value_for_currency()` returns None (currency config mismatch) | `DISCOUNT_VALUE_NOT_RESOLVED` | discounts | `codes[{i}].code` | `{code, currency}` | L746-754: `value_for_currency = None` → `discount_extra_value_rest = None` → no discount applied silently |

### Phase 7: Cascading Effects on Other Scopes

Discounts can trigger errors in other scopes:

| # | Scenario | v2 error code | scope | meta | Why |
|---|----------|---------------|-------|------|-----|
| 32 | Discount reduces cart total below `min_order_price` | `MIN_ORDER_NOT_MET` | cart | `{min, current}` | `validators/cart.py` L798-800: `total_based_on < channel.min_order_price` |
| 33 | Discount grants `free_shipping` but selected method not in `free_shipping_methods` | (not an error — shipping cost stays) | — | — | L577-589: `shipping_method.code in free_shipping_methods` just toggles `free_shipping=True/False` |
| 34 | Discount makes `free_order=True` → total becomes 0 → payment method may become irrelevant | (info, not error) | — | — | Edge case: front should handle `total_gross: "0.00"` gracefully |

### Implementation Notes

**v2 error mapper pattern:**

```python
# services/discount_error_mapper.py

def map_discount_errors(validated_discounts: list, cart_data) -> list[dict]:
    """Convert ValidatedDiscountData with INVALID status to v2 error details."""
    details = []
    for i, (discount_data, rule) in enumerate(validated_discounts):
        if discount_data.status == ItemStatus.INVALID:
            code, meta = infer_discount_error(discount_data, rule, cart_data)
            details.append({
                "scope": "discounts",
                "field": f"codes[{i}].code",
                "code": code,
                "message": DISCOUNT_ERROR_MESSAGES[code].format(**meta),
                "meta": meta,
            })
    return details
```

**Problem in v1:** `factor_invalid_discount()` returns a generic INVALID status with zero values
but no reason code. The v2 mapper must **infer** why the discount was rejected based on:
1. Was the code found in DB at all? (query results)
2. Was the rule filtered out by channel/target/customer? (compare `base_query` vs `query`)
3. Was it a currency/stacking/usage limit issue? (specific checks in L235-314)

**Recommended approach:** Instead of inferring from the opaque INVALID status, add a `rejection_reason`
field to `ValidatedDiscountData` in v1 domain. Each check that leads to `factor_invalid_discount()`
or `status = INVALID` sets the reason. v2 mapper reads it directly. This is a **non-breaking** v1 change
(new optional field on an internal DTO, not exposed in v1 API response).

```python
# Add to ValidatedDiscountData
rejection_reason: str | None = None  # e.g., "code_not_found", "expired", "usage_limit"
rejection_meta: dict | None = None   # e.g., {"expired_at": "2025-12-31"}
```

### Frontend Recovery Actions

| v2 error code | Frontend action |
|---------------|-----------------|
| `DISCOUNT_CODE_NOT_FOUND` | "Invalid code. Check spelling." |
| `DISCOUNT_EXPIRED` | "Code expired on {expired_at}." — remove code |
| `DISCOUNT_NOT_YET_ACTIVE` | "Code activates on {active_from}." |
| `DISCOUNT_USAGE_LIMIT_REACHED` | "Code fully redeemed." — remove code |
| `DISCOUNT_PER_USER_LIMIT_REACHED` | "You've already used this code." — remove code |
| `DISCOUNT_MIN_ORDER_NOT_MET` | "Add {diff} more to use this code." |
| `DISCOUNT_LOGIN_REQUIRED` | "Log in to use this code." — show login prompt |
| `DISCOUNT_FIRST_ORDER_ONLY` | "Code valid for first orders only." — remove code |
| `DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE` | "Cannot combine with {blocking_code}." |
| `DISCOUNT_NO_ELIGIBLE_PRODUCTS` | "Code doesn't apply to items in your cart." |
| `GRATIS_REQUIRED_SKUS_MISSING` | "Add {missing_skus} to qualify for free product." |
| `GRATIS_SKU_NOT_ALLOWED` | "Selected product not eligible for free gift." — show picker |
| `GRATIS_QTY_EXCEEDED` | "Maximum {max_qty} free items." — adjust qty |
| `GRATIS_TIER_NOT_REACHED` | "Add {diff} more to unlock free gift." |

---

## Next steps

1. ~~Define canonical Address shape in api-response-contract.md~~ Done
2. ~~Design detailed cart response v2 JSON~~ Done (in this doc)
3. ~~Design order response v2 shape~~ Done (Order Creation/List/Detail sections)
4. ~~Decide on v1/v2 coexistence~~ Done (v1/v2 Coexistence Rule section)
5. ~~Error code taxonomy — complete list per scope with meta fields~~ Done (Discount Error Taxonomy, Item Statuses)
6. Design admin order detail response (separate from public order detail)
7. Error taxonomy for non-discount scopes (items, addresses, shipping, payment) — same depth as discount taxonomy
8. Remove legacy DeliveryPoint from checkout (cleanup task — see Delivery Points section)
9. Implement `rejection_reason` on ValidatedDiscountData (non-breaking v1 change, enables v2 error mapper)
10. Pydantic schema definitions — cross-check each against v1 DTO using Completeness Rule
