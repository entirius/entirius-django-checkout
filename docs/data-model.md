# Data model

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
