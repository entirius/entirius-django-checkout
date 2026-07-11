# CART EDIT

```
{
    "meta": {
        "status": "FAIL",
        "message": "You need shipping address to add shipping. "
    },
    "data": {
        "cart": {
            "items": [
                {
                    [...]
                }
            ],
            "discount_amount": "discount_amount",
            "discounts": [],
            "base_total_price": "base_total_price",
            "total_price": "total_price",
            "validation_status": "valid"
        },
        "addresses": {
            "shipping_address": null,
            "billing_address": {
                [...]
            },
            "validation_status": "invalid"
        },
        "payment_method": {
            [...]
        },
        "shipping_method": null,
        "total_to_min_order_price": "1.00",
        "base_total": "base_total",
        "total": "total",
        "language_code": "language_code",
        "currency_code": "currency_code",
        "country_code": "country_code",
        "comment": comment,
        "validation_status": "invalid",
        "cart_id": "cart_id",
        "cart_status": "cart_status",
        "free_shipping": free_shipping,
        "amount_required_for_free_shipping": "amount_required_for_free_shipping",
        "amount_missing_for_free_shipping": "amount_missing_for_free_shipping"
    }
} status=200

{
    "meta": {
        "status": "FAIL",
        "message": "You need billing address to add payment. "
    },
    "data": {
        "cart": {
            "items": [
                {
                    [...]
                }
            ],
            "discount_amount": "discount_amount",
            "discounts": [],
            "base_total_price": "base_total_price",
            "total_price": "total_price",
            "validation_status": "valid"
        },
        "addresses": {
            "shipping_address": {
                [...]
            },
            "billing_address": null,
            "validation_status": "invalid"
        },
        "payment_method": {
            [...]
        },
        "shipping_method": {
            [...]
        },
        "total_to_min_order_price": "1.00",
        "base_total": "base_total",
        "total": "total",
        "language_code": "language_code",
        "currency_code": "currency_code",
        "country_code": "country_code",
        "comment": comment,
        "validation_status": "invalid",
        "cart_id": "cart_id",
        "cart_status": "cart_status",
        "free_shipping": free_shipping,
        "amount_required_for_free_shipping": "amount_required_for_free_shipping",
        "amount_missing_for_free_shipping": "amount_missing_for_free_shipping"
    }
} status=200

{
    "meta": {
        "status": "BAD_REQUEST",
        "message": "Validation error"
    },
    "data": {
        "json": {
            "addresses": {"billing_address": {"street": ["Missing data for required field."]}}
        }
    }
} status=400

{
    "meta": {
        "status": "BAD_REQUEST",
        "message": "Validation error"
    },
    "data": {
        "json": {
            "addresses": {"shipping_address": {"street": ["Missing data for required field."]}}
        }
    }
} status=400
```

# POST ORDER

```
    {"status": "ADDRESSES_NOT_SELECTED", "message": "There is no addresses in this cart c230fc10-a122-42b1-9b1a-76e69dcb12e9"} status=403
    {"status": "SHIPPING_OPTION_NOT_SELECTED", "message": "There is no shipping method in this cart 0213cf48-906e-4512-aedf-3d2c15ac4f91"} status=403
    {"status": "PAYMENT_OPTION_NOT_SELECTED", "message": "There is no payment method in this cart 0213cf48-906e-4512-aedf-3d2c15ac4f91"} status=403

```

