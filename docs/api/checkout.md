# Checkout

Table of Contents
=================

  * [Kolekcja Postman](#kolekcja-postman)
  * [Interfaces](#interfaces)
    * [Item](#item)
    * [Discount](#discount)
    * [Cart](#cart)
    * [Shipping Address](#shipping-address)
    * [Billing address](#billing-address)
    * [Addresses](#addresses)
    * [Shipping method](#shipping-method)
    * [Delivery point](#delivery-point)
    * [Payment method](#payment-method)
  * [Item status](#item-status)
  * [Validation status](#validation-status)
  * CART
    * [POST Cart](#post-cart)
    * [PUT Cart](#put-cart)
    * [PATCH Cart](#patch-cart)
    * [GET Cart](#get-cart)
    * [CLOSE Cart](#close-cart)
    * [GET Latest Cart for logged in user](#get-latest-cart-for-logged-in-user)
    * [GET Cart available shipping methods](#get-cart-available-shipping-methods)
    * [GET Cart available payment methods](#get-cart-available-payment-methods)
    * [GET Cart available delivery points](#get-cart-available-delivery-points)
  * ORDER
    * [POST Order](#post-order)
    * [GET Detail Order](#get-detail-order)
    * [GET Listing Order](#get-listing-order)
    * [GET Order Attachment](#get-order-attachment)
  * [GET Countries](#get-countries)

## Kolekcja Postman
W docs została zapisana kolekcja z Postman możliwa do importu. 
Kolekcja zawiera automatyzację zapisywania cart-id oraz order-id do variables. 
Nie trzeba ich ręcznie podmieniać, wystarczy używać zapisanych metod z zmianami w body.
Aby poprawnie użyć kolekcji należy w jej variables edytować host, channel i api-key.
Metody związane z pozycjami zamówienia korzystają z zmiennych sku i qty.
Pozostałe zmienne nadpisują się automatycznie.
![postman.png](media/postman.png)

## Interfaces

>Przykładowy minimalny koszyk
```json
{
    "cart": {
        "items": [
            {"sku": "sku-1", "quantity": 1}
        ]
    }
}
```

>Przykładowe pełne dane do checkoutu
```json
{
    "cart": {
        "items": [
            {"sku": "sku-1", "quantity": 1}
        ],
        "discounts": [
            {"code": "test"}
        ]
    },
    "addresses": {
        "shipping_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": ""
        },
        "billing_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": "",
            "requested_invoice": false
        }
    },
    "shipping_method": {
        "code": "kurier"
    }, 
    "payment_method": {
        "code": "przelew"
    },
    "split_order": false,
    "comment": "test"
}
```

### Item

| Field    | Description                                        | Optional |
|----------|----------------------------------------------------|----------|
| sku      | string representing product sku                    |          |
| quantity | number representing requested quantity of the item |          |

### Discount

| Field | Description                         | Optional |
|-------|-------------------------------------|----------|
| code  | string representing a discount code |          |

### Cart

| Field       | Description                     | Optional |
|-------------|---------------------------------|----------|
| items       | list of item objects            |          |
| discounts   | list of discount objects        | +        |
| split_order | cart should be splitted boolean | +        |
| comment     | comment to cart                 | +        |

### Shipping Address

| Field         | Description                           | Optional |
|---------------|---------------------------------------|----------|
| firstname     | string                                |          |
| lastname      | string                                |          |
| country_code  | string representing country iso2 code |          |
| city          | string                                |          |
| postcode      | string                                |          |
| street        | list of strings                       |          |
| dialling_code | string                                |          |
| telephone     | string                                |          |
| email         | a valid email address                 |          |
| company       | string                                | +        |
| address_id    | string                                | +        |
| external_id   | string                                | +        |

### Billing address

same as shipping address plus:

| Field             | Description | Optional |
|-------------------|-------------|----------|
| tax_id            | string      | +        |
| requested_invoice | boolean     |          |


### Addresses

| Field            | Description             | Optional |
|------------------|-------------------------|----------|
| shipping_address | shipping address object | +        |
| billing_address  | billing address object  | +        |

### Shipping method

| Field          | Description                                                                           | Optional |
|----------------|---------------------------------------------------------------------------------------|----------|
| code           | string representing an unique identifier                                              |          |
| country_code   | string for country code if no addres is given yet, only "ALL" can skip adress missing | +        |
| delivery_point | object representing chosen delivery point                                             | +        |

### Delivery point

| Field    | Description                      | Optional |
|----------|----------------------------------|----------|
| name     | delivery point name              |          |
| address  | delivery point address as string |          |
| postcode | delivery point post code         |          |
| city     | city name as string              | +        |

### Payment method

| Field        | Description                                                 | Optional |
|--------------|-------------------------------------------------------------|----------|
| code         | string representing an unique identifier                    |          |
| continue_url | string representing link to redirect from payment sytem     | +        |
| card         | string representing value of payment (ex. for payu - token) | +        |
| save_card    | boolean, Should a credit card be saved?                     | +        |

## Item status

| Status                | Description |
|-----------------------|-------------|
| valid                 |             |
| changed_qty           |             |
| changed_price         |             |
| changed_price_and_qty |             |
| out_of_stock          |             |
| invalid               |             |

## Validation status

| Status  | Description                                                                       |
|---------|-----------------------------------------------------------------------------------|
| valid   | cart or checkout is valid                                                         |
| changed | cart or checkout changed in relation to the previous state but is otherwise valid |
| invalid | cart or checkout is invalid and requires changes                                  |

## POST Cart

Pozwala utworzyć nowy koszyk, zwraca wyliczenie na podstawie przesłanych danych


```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/
# Przykładowe zapytanie:
curl -X POST {{<base_url>}}/checkout/v1/<channel_idx>/carts/
```

>Przykładowa treść zapytania
```json
{
  "cart": {
    "items": [
      {"sku": "sku-1", "quantity": 1},
      {"sku": "sku-2", "quantity": 1}
    ]
  }
}
```

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "CREATED",
        "message": ""
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "quantity": "1",
                    "discount_amount": "0.00",
                    "discount_percent": 10,
                    "base_unit_price": "27.48",
                    "base_total_price": "27.48",
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60",
                    "unit_price": "24.60",
                    "total_price": "24.60",
                    "status": "valid", 
                    "is_gratis": false
                  
                },
                {
                    "sku": "sku-2",
                    "quantity": 0,
                    "discount_amount": 0,
                    "discount_percent": 0,
                    "base_unit_price": 0,
                    "base_total_price": 0,
                    "special_from_date": null,
                    "special_to_date": null,
                    "special_unit_price": 0,
                    "special_total_price": 0,
                    "unit_price": 0,
                    "total_price": 0,
                    "status": "invalid",
                    "is_gratis": false
                }
            ],
            "discount_amount": "0.00",
            "discounts": [],
            "base_total_price": "27.48",
            "total_price": "24.60",
            "available_gratis_rules": [],
            "validation_status": "invalid"
        },
        "addresses": null,
        "payment_method": null,
        "shipping_method": null,
        "base_total": "27.48",
        "total": "24.60",
        "language_code": "pl",
        "currency_code": "PLN",
        "country_code": "PL",
        "comment": null,
        "split_order": false,
        "extra": {},
        "need_full_address": true,
        "can_be_split": true,
        "split_by_feature_idx": null,
        "split_by_attr_idx": null,
        "original_order_id": "3d311d1d-1173-49c1-be5c-f57cdc84e26c",
        "validation_status": "invalid",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "new",

        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "125.40",
        "amount_required_for_free_shipping_above_modifier": "4000.00",
        "amount_missing_for_free_shipping_above_modifier": "3410.00",
        "min_order_price": "200.00"
    }
}
```

| Field           | Description                            | Optional |
|-----------------|----------------------------------------|----------|
| cart            | cart object                            |          |
| addresses       | addresses object                       | +        |
| shipping_method | shipping method object                 | +        |
| payment_method  | payment method object                  | +        |
| currency_code   | string representing currency iso2 code | +        |
| country_code    | string representing language iso2 code | +        |
| language_code   | string representing language iso3 code | +        |
| custom_order_id | Custom order id                        | +        |
| comment         | customer comment                       | +        |

W przypadku braku informacji o currency_code, country_code, language_code zostanie ustawiona wartość deafaultowa z channela.

## PUT Cart

Pozwala podmienić dane całego koszyka i dokonać ponownego przeliczenia już istniejącego koszyka. Metoda PUT uważa za święte to co jest w request. Jeśli koszyk miał jakieś dane które nie przyjdą w request zostane nadpisane pustym polem.

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X PUT {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

>Przykładowa treść zapytania
```json
{
    "cart": {
        "items": [
            {"sku": "sku-1", "quantity": 1}
        ],
        "discounts": [
            {"code": "test"}
        ]
    },
    "addresses": {
        "shipping_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": ""
        },
        "billing_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": "",
            "requested_invoice": false
        }
    },
    "shipping_method": {
        "code": "kurier"
    }, 
    "payment_method": {
        "code": "przelew",
        "continue_url": "https://url.do.ktorego.wrocisz.po.platnosci"
    }
}
```

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "quantity": "1",
                    "discount_amount": "10.00",
                    "discount_percent": 10,
                    "total_weight": "15.00",
                    "base_unit_price": "27.48",
                    "base_total_price": "27.48",
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60",
                    "unit_price": "24.60",
                    "total_price": "14.60",
                    "status": "valid"
                }
            ],
            "discount_amount": "10.00",
            "discounts": [
                {
                    "code": "test",
                    "item_code": [
                        "sku-1"
                    ],
                    "price_discount": 0,
                    "percent_discount": 12,
                    "free_shipping": false,
                    "status": "valid",
                    "free_order": false,
                    "min_order_amount": "0.00",
                    "extra_value": 12,
                    "target": "all",
                    "modifier": "percent_discount"
                }
            ],
            "base_total_price": "27.48",
            "total_price": "14.60",
            "available_gratis_rules": [],
            "total_weight": "15.00",
            "validation_status": "valid"
        },
        "addresses": {
            "shipping_address": {
                "email": "customer@example.com",
                "firstname": "Test",
                "lastname": "Testowicz",
                "country_code": "PL",
                "city": "Warsaw",
                "postcode": "00-000",
                "street": "test street 89",
                "dialling_code": "+48",
                "telephone": "500500500",
                "company": ""
            },
            "billing_address": {
                "email": "customer@example.com",
                "firstname": "Test",
                "lastname": "Testowicz",
                "country_code": "PL",
                "city": "Warsaw",
                "postcode": "00-000",
                "street": "test street 89",
                "dialling_code": "+48",
                "telephone": "500500500",
                "company": "",
                "requested_invoice": false,
                "tax_id": null
            },
            "validation_status": "valid"
        },
        "payment_method": {
            "code": "przelew",
            "name": "Przelew Bankowy",
            "card": null,
            "bank_id": null,
            "country_code": "PL",
            "authorization_token": null,
            "is_cash_on_delivery": false,
            "status": "valid"
        },
        "shipping_method": {
            "code": "kurier",
            "name": "Kurier",
            "country_code": "PL",
            "delivery_point": null,
            "base_unit_price": "20.00",
            "base_total_price": "20.00",
            "discount_amount": "0.00",
            "unit_price": "20.00",
            "total_price": "20.00",
            "cash_on_delivery_fee": null,
            "free_delivery_above": "150.00",
            "status": "valid"
        },
        "base_total": "47.48",
        "total": "34.60",
        "language_code": "pl",
        "currency_code": "PLN",
        "country_code": "PL",
        "comment": null,
        "split_order": false,
        "extra": {},
        "need_full_address": true,
        "can_be_split": true,
        "split_by_feature_idx": null,
        "split_by_attr_idx": null,
        "original_order_id": "3d311d1d-1173-49c1-be5c-f57cdc84e26c",
        "validation_status": "valid",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "new",
        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "135.40",
         "amount_required_for_free_shipping_above_modifier": "4000.00",
        "amount_missing_for_free_shipping_above_modifier": "3410.00",
        "min_order_price": "200.00"
    }
}
```

| Field           | Description                            | Optional |
|-----------------|----------------------------------------|----------|
| cart            | cart object                            |          |
| addresses       | addresses object                       | +        |
| shipping_method | shipping method object                 | +        |
| payment_method  | payment method object                  | +        |
| currency_code   | string representing currency iso2 code | +        |
| language_code   | string representing language iso3 code | +        |
| country_code    | string representing language iso2 code | +        |
| custom_order_id | Custom order id                        | +        |
| comment         | customer comment                       | +        |

## PATCH Cart

Pozwala podmienić dane pojedynczych informacji koszyka i dokonać ponownego przeliczenia już istniejącego koszyka. Metoda PATCH nadpisuje tylko wysłane dane, pozostałych nie rusza.

Możliwe dane do wysłania osobno. Sekcje można łączyć i wysyłać w dowolnej kolejności. Założeniem jest że posiadamy cart_id. A utworzenie cart wymaga podania co najmniej jednego itema.
- cart
    - items
    - discounts
- adresses
  - billing address
  - shipping address
- shipping method
- payment method

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X PATCH {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

>Przykładowa treść zapytania `cart items`
```json
{
    "cart": {
        "items": [
            {"sku": "sku-1", "quantity": 1}
        ]
    }
}
```

>Przykładowa treść zapytania `cart discounts`
```json
{
    "cart": {
        "discounts": [
            {"code": "test"}
        ]
    }
}
```

>Przykładowa treść zapytania `address billing`
```json
{
    "addresses": {
        "billing_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": "",
            "requested_invoice": false
        }
    }
}
```

>Przykładowa treść zapytania `address shipping`
```json
{
    "addresses": {
        "shipping_address": {
            "firstname": "Test",
            "lastname": "Testowicz",
            "country_code": "PL",
            "city": "Warsaw",
            "postcode": "00-000",
            "street": "test street 89",
            "dialling_code": "+48",
            "telephone": "500500500",
            "email": "customer@example.com",
            "company": ""
        }
    }
}
```

>Przykładowa treść zapytania `shipping method`
```json
{
    "shipping_method": {
        "code": "kurier"
    }
}
```

>Przykładowa treść zapytania `payment method`
```json
{
    "payment_method": {
        "code": "przelew",
        "continue_url": "https://url.do.ktorego.wrocisz.po.platnosci"
    }
}
```

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "quantity": "1",
                    "discount_amount": "10.00",
                    "discount_percent": 10,
                    "base_unit_price": "27.48",
                    "base_total_price": "27.48",
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60",
                    "unit_price": "24.60",
                    "total_price": "14.60",
                  "total_weight": "15.00",
                    "status": "valid"
                }
            ],
            "discount_amount": "10.00",
            "discounts": [
                {
                    "code": "test",
                    "item_code": [
                        "sku-1"
                    ],
                    "price_discount": 0,
                    "percent_discount": 12,
                    "free_shipping": false,
                    "status": "valid",
                    "free_order": false,
                    "min_order_amount": "0.00",
                    "extra_value": 12,
                    "target": "all",
                    "modifier": "percent_discount"
                }
            ],
            "base_total_price": "27.48",
            "total_price": "14.60",
            "available_gratis_rules": [],
            "total_weight": "15.00",
            "validation_status": "valid"
        },
        "addresses": {
            "shipping_address": {
                "email": "customer@example.com",
                "firstname": "Test",
                "lastname": "Testowicz",
                "country_code": "PL",
                "city": "Warsaw",
                "postcode": "00-000",
                "street": "test street 89",
                "dialling_code": "+48",
                "telephone": "500500500",
                "company": ""
            },
            "billing_address": {
                "email": "customer@example.com",
                "firstname": "Test",
                "lastname": "Testowicz",
                "country_code": "PL",
                "city": "Warsaw",
                "postcode": "00-000",
                "street": "test street 89",
                "dialling_code": "+48",
                "telephone": "500500500",
                "company": "",
                "requested_invoice": false,
                "tax_id": null
            },
            "validation_status": "valid"
        },
        "payment_method": {
            "code": "przelew",
            "name": "Przelew Bankowy",
            "card": null,
            "bank_id": null,
            "authorization_token": null,
            "is_cash_on_delivery": false,
            "status": "valid",
            "country_code": "PL"      
        },
        "shipping_method": {
            "code": "kurier",
            "name": "Kurier",
            "country_code": "PL",
            "delivery_point": null,
            "base_unit_price": "20.00",
            "base_total_price": "20.00",
            "discount_amount": "0.00",
            "unit_price": "20.00",
            "total_price": "20.00",
            "cash_on_delivery_fee": null,
            "free_delivery_above": "150.00",
            "status": "valid"
        },
        "base_total": "47.48",
        "total": "34.60",
        "language_code": "pl",
        "currency_code": "PLN",
        "country_code": "PL",
        "comment": null,
        "split_order": false,
        "extra": {},
        "need_full_address": true,
        "can_be_split": true,
        "split_by_feature_idx": null,
        "split_by_attr_idx": null,
        "original_order_id": "3d311d1d-1173-49c1-be5c-f57cdc84e26c",
        "validation_status": "valid",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "new",
        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "135.40",
        "amount_required_for_free_shipping_above_modifier": "4000.00",
        "amount_missing_for_free_shipping_above_modifier": "3410.00",
        "min_order_price": "200.00"
    }
}
```

| Field           | Description                            | Optional |
|-----------------|----------------------------------------|----------|
| cart            | cart object                            |          |
| addresses       | addresses object                       | +        |
| shipping_method | shipping method object                 | +        |
| payment_method  | payment method object                  | +        |
| currency_code   | string representing currency iso2 code | +        |
| language_code   | string representing language iso3 code | +        |
| country_code    | string representing language iso2 code | +        |
| custom_order_id | Custom order id                        | +        |
| comment         | customer comment                       | +        |

## GET Cart

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

Pozwala pobrać dane dotyczące koszyka/checkoutu na podstawie cart_id

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "status": "valid",
                    "quantity": "1",
                    "unit_price": "24.60",
                    "total_price": "14.60",
                    "total_weight": "15.00",
                    "base_unit_price": "27.48",
                    "discount_amount": "10.00",
                    "base_total_price": "27.48",
                    "discount_percent": 10,
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60"
                }
            ],
            "discounts": [
                {
                    "code": "test",
                    "item_code": [
                        "sku-1"
                    ],
                    "price_discount": 0,
                    "percent_discount": 12,
                    "free_shipping": false,
                    "status": "valid",
                    "free_order": false,
                    "min_order_amount": "0.00",
                    "extra_value": 12,
                    "target": "all",
                    "modifier": "percent_discount"
                }
            ],
            "total_price": "14.60",
            "discount_amount": "10.00",
            "available_gratis_rules": [],
            "total_weight": "15.00",
            "base_total_price": "27.48",
            "validation_status": "valid"
        },
        "addresses": {
            "billing_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "tax_id": null,
                "company": "",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48",
                "requested_invoice": false
            },
            "shipping_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "company": "",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48"
            },
            "validation_status": "valid"
        },
        "payment_method": {
            "card": null,
            "code": "przelew",
            "name": "Przelew Bankowy",
            "status": "valid",
            "bank_id": null,
            "authorization_token": null,
            "is_cash_on_delivery": false
        },
        "shipping_method": {
            "code": "kurier",
            "name": "Kurier",
            "status": "valid",
            "unit_price": "20.00",
            "total_price": "20.00",
            "country_code": "PL",
            "delivery_point": null,
            "base_unit_price": "20.00",
            "discount_amount": "0.00",
            "base_total_price": "20.00",
            "free_delivery_above": "150.00",
            "cash_on_delivery_fee": null
        },
        "base_total": "47.48",
        "total": "34.60",
        "language_code": "pl",
        "currency_code": "PLN",            
        "country_code": "PL",
        "comment": null,
        "split_order": false,
        "extra": {},
        "need_full_address": true,
        "can_be_split": true,
        "split_by_feature_idx": null,
        "split_by_attr_idx": null,
        "original_order_id": "3d311d1d-1173-49c1-be5c-f57cdc84e26c",
        "validation_status": "valid",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "new",
        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "135.40",
        "amount_required_for_free_shipping_above_modifier": "4000.00",
        "amount_missing_for_free_shipping_above_modifier": "3410.00",
        "min_order_price": "200.00"
    }
}
```

## CLOSE Cart

Dodano w wersji 4.1.0

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X DELETE {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

Pozwala zamknąć wybrany koszyk na podstawie cart_id

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": "Cart closed successfully"
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "status": "valid",
                    "quantity": "1",
                    "unit_price": "24.60",
                    "total_price": "14.60",
                    "base_unit_price": "27.48",
                    "discount_amount": "10.00",
                    "base_total_price": "27.48",
                    "discount_percent": 10,
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60",
                    "unit_price_netto": "17.60",
                    "total_price_netto": "17.60"
                }
            ],
            "discounts": [],
            "total_price": "14.60",
            "discount_amount": "10.00",
            "base_total_price": "27.48",
            "validation_status": "valid",
            "total_netto_price": "17.60",
            "tax_amount": "8.00"
        },
        "addresses": {
            "billing_address": null,
            "shipping_address": null,
            "validation_status": "valid"
        },
        "payment_method": null,
        "shipping_method": null,
        "total_to_min_order_price": "1.00",
        "base_total": "47.48",
        "total": "34.60",
        "language_code": "pl",
        "currency_code": "PLN",            
        "country_code": "PL",
        "comment": null,
        "fee_price": "0",
        "total_tax": "2",
        "custom_order_id": "A00000001",
        "validation_status": "valid",
        "requested_delivery_date": "2023-05-06",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "closed",
        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "135.40"
    }
}
```

## GET Latest Cart for logged in user

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/customer/cart/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/customer/cart/ \
--header 'Authorization: Bearer <customer_token>' \
--header 'x-api-key: <checkout_api_key>'
```

Pozwala pobrać dane dotyczące ostatniego, możliwego do przeprocesowania koszyka na podstawie zalogowanego usera

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "sku-1",
                    "status": "valid",
                    "quantity": "1",
                    "unit_price": "24.60",
                    "total_price": "14.60",
                    "total_weight": "15.00",
                    "base_unit_price": "27.48",
                    "discount_amount": "10.00",
                    "base_total_price": "27.48",
                    "discount_percent": 10,
                    "special_from_date": "2019-12-12",
                    "special_to_date": "2019-12-27",
                    "special_unit_price": "24.60",
                    "special_total_price": "24.60"
                }
            ],
            "discounts": [
                {
                    "code": "test",
                    "item_code": [
                        "sku-1"
                    ],
                    "price_discount": 0,
                    "percent_discount": 12,
                    "free_shipping": false,
                    "status": "valid",
                    "free_order": false,
                    "min_order_amount": "0.00",
                    "extra_value": 12,
                    "target": "all",
                    "modifier": "percent_discount"
                }
            ],
            "total_price": "14.60",
            "discount_amount": "10.00",
            "available_gratis_rules": [],
            "total_weight": "15.00",
            "base_total_price": "27.48",
            "validation_status": "valid"
        },
        "addresses": {
            "billing_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "tax_id": null,
                "company": "",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48",
                "requested_invoice": false
            },
            "shipping_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "company": "",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48"
            },
            "validation_status": "valid"
        },
        "payment_method": {
            "card": null,
            "code": "przelew",
            "name": "Przelew Bankowy",
            "status": "valid",
            "bank_id": null,
            "authorization_token": null,
            "is_cash_on_delivery": false
        },
        "shipping_method": {
            "code": "kurier",
            "name": "Kurier",
            "status": "valid",
            "unit_price": "20.00",
            "total_price": "20.00",
            "country_code": "PL",
            "delivery_point": null,
            "base_unit_price": "20.00",
            "discount_amount": "0.00",
            "base_total_price": "20.00",
            "free_delivery_above": "150.00",
            "cash_on_delivery_fee": null
        },
        "base_total": "47.48",
        "total": "34.60",
        "language_code": "pl",
        "currency_code": "PLN",            
        "country_code": "PL",
        "comment": null,
        "split_order": false,
        "extra": {},
        "need_full_address": true,
        "can_be_split": true,
        "split_by_feature_idx": null,
        "split_by_attr_idx": null,
        "original_order_id": "3d311d1d-1173-49c1-be5c-f57cdc84e26c",
        "validation_status": "valid",
        "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c",
        "cart_status": "new",
        "free_shipping": false,
        "amount_required_for_free_shipping": "150.00",
        "amount_missing_for_free_shipping": "135.40",
         "amount_required_for_free_shipping_above_modifier": "4000.00",
        "amount_missing_for_free_shipping_above_modifier": "3410.00",
        "min_order_price": "200.00"
    }
}
```

## GET Cart available shipping methods

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/shipping-methods/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/shipping-methods/
```

Pozwala pobrać dane dotyczące dozwolonych metod dostawy dla danego koszyka

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": [
        {
            "country_code": "PL",
            "price_brutto": "20.00",
            "free_delivery_above_brutto": "150.00",
            "new_price": "0.00",
            "cash_on_delivery_available": false,
            "cash_on_delivery_fee": null,
            "code": "kurier",
            "currency_code": "PLN",
            "method_type": "default",
            "name": "Kurier",
            "description": null,
            "image": "/media/"
        },
        {
            "country_code": "ALL",
            "price_brutto": "30.00",
            "free_delivery_above_brutto": "150.00",
            "new_price": "0.00",
            "cash_on_delivery_available": false,
            "cash_on_delivery_fee": null,
            "code": "kurier",
            "currency_code": "PLN",
            "method_type": "default",
            "name": "Kurier",
            "description": null,
            "image": "/media/"
        }
    ],
    "pagination": {
        "page": 1,
        "limit": 10,
        "pages": 1,
        "records": 2
    }
}
```

## GET Cart available payment methods

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/orders/<cart_id>/payment-methods/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/payment-methods/
```

Pozwala pobrać dane dotyczące dozwolonych metod płatności dla danego koszyka

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": [
        {
            "code": "przelew",
            "provider": "transfer",
            "is_cash_on_delivery": false,
            "cash_on_delivery_fee": "0",
            "name": "Przelew Bankowy",
            "description": null
        }
    ],
    "pagination": {
        "page": 1,
        "limit": 10,
        "pages": 1,
        "records": 1
    }
}
```

| Provider         | Description                                                |
|------------------|------------------------------------------------------------|
| cod              | cash on delivery                                           |
| tpay_transaction | Tpay transaction API (tutaj fromularz z wyborem banków)    |
| tpay_card        | Tpay cards API (tutaj formularz z danymi karty płatniczej) |

| Field | Description                                                                                |
|-------|--------------------------------------------------------------------------------------------|
| banks | list of objects representing available banks, present only if provider is tpay_transaction |

## GET Cart available delivery points

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/orders/<cart_id>/delivery-points/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/delivery-points/
```

Pozwala pobrać dane dotyczące dozwolonych punktów odbioru dla koszyka, wymaga uwcześniejszego wyboru metody dostawy

>Przykładowa odpowiedź
```json
{
  "meta": {
    "status": "OK",
    "message": ""
  },
  "data": [
    {
      "idx": "CC149",
      "city": "Warszawa",
      "postcode": "02-021",
      "street": "Grójecka 17",
      "point_type": "pok, pop",
      "point_description": "Punkt Małpki  ",
      "latitude": "52.2221",
      "longitude": "20.9863",
      "country_code": "PL"
    },
    {
      "idx": "POP-WAW257",
      "city": "Warszawa",
      "postcode": "02-120",
      "street": "Grójecka 97",
      "point_type": "pok, pop",
      "point_description": "LEJDIS GARDEN Punkt ",
      "latitude": "52.2089",
      "longitude": "20.9753",
      "country_code": "PL"
    },
    {
      "idx": "POP-WAW258",
      "city": "Warszawa",
      "postcode": "02-094",
      "street": "Grójecka 67",
      "point_type": "pok, pop",
      "point_description": "KSIĘGARNIA EKONOMICZNA Punkt ",
      "latitude": "52.2135",
      "longitude": "20.9779",
      "country_code": "PL"
    },
    {
      "idx": "WAW13HO",
      "city": "Warszawa",
      "postcode": "02-031",
      "street": "Grójecka 47",
      "point_type": "parcel_locker",
      "point_description": "W sklepie w wejściu  ",
      "latitude": "52.2158",
      "longitude": "20.9812",
      "country_code": "PL"
    },
    {
      "idx": "WAW194AP",
      "city": "Warszawa",
      "postcode": "02-124",
      "street": "Grójecka 125",
      "point_type": "parcel_locker",
      "point_description": "Stacja paliw BP  BP - Pralniomat HiShine",
      "latitude": "52.2010",
      "longitude": "20.9686",
      "country_code": "PL"
    },
    {
      "idx": "WAW42N",
      "city": "Warszawa",
      "postcode": "02-101",
      "street": "Grójecka 95",
      "point_type": "parcel_locker",
      "point_description": "Przy Hale Banacha  Hale Banacha",
      "latitude": "52.2086",
      "longitude": "20.9771",
      "country_code": "PL"
    },
    {
      "idx": "WAW57A",
      "city": "Warszawa",
      "postcode": "02-124",
      "street": "Grójecka 125",
      "point_type": "parcel_locker",
      "point_description": "Stacja paliw BP  BP",
      "latitude": "52.2011",
      "longitude": "20.9687",
      "country_code": "PL"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 10,
    "pages": 1,
    "records": 7
  }
}
```

| Parameter | Description |
|-----------|-------------|
| city      | (str)       |
| postcode  | (str)       |
| street    | (str)       |

## POST Order

Pozwala utworzyć nowy nowe zamówienie.
Jeżeli zamówienie jest poprawne i wyliczona cena się zgadza, zostaje utworzone nowe zamówienie.
W przeciwnym wypadku zostaje zwrócona informacja o niepoprawnych/niepełnych danych lub zmianie wyliczeń.


```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/orders/
# Przykładowe zapytanie:
curl -X POST {{<base_url>}}/checkout/v1/<channel_idx>/orders/
```

>Przykładowa treść zapytania
```json
{
    "cart_id": "27d05ed6-1173-49c1-be5c-f57cdc84e26c"
}
```

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "CREATED",
        "message": ""
    },
    "data": {
        "order_id": "9f991fce-e880-4ed0-8e82-775e18e1d9b1",
        "order_status": "new",
        "order_pretty_id": "1000000002",
        "redirect_url": null
    }
}
```

| Field   | Description | Optional |
|---------|-------------|----------|
| cart_id | cart_id     |          |

## GET Detail Order

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/orders/<order_id>/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/orders/<order_id>/
```

Zwraca pojedyńcze zamówienie, wymaga podania identyfikatora zamówienia oraz autoryzacji tokenem typu access.  W przypadku gdy użytkownik przypisany do zamówienia nie będzie miał do niego uprawnień zostanie zwrócony komunikat FORBBIDEN z kodem 403

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": "",
        "messages": [],
        "regional": {
            "language": null,
            "currency": null,
            "country": null
        }
    },
    "data": {
        "cart": {
            "items": [
                {
                    "sku": "complex-simple-black40zamek-z-prawej19",
                    "name": "complex-simple-black40zamek-z-prawej19",
                    "status": "valid",
                    "quantity": "6",
                    "tax_rate": "0.23",
                    "is_gratis": false,
                    "unit_price": "100.00",
                    "total_price": "600.00",
                    "base_unit_price": "100.00",
                    "discount_amount": "0.00",
                    "special_percent": null,
                    "special_to_date": null,
                    "unit_tax_amount": "18.70",
                    "base_total_price": "600.00",
                    "discount_percent": null,
                    "total_tax_amount": "112.20",
                    "unit_price_netto": "81.30",
                    "special_from_date": null,
                    "total_price_netto": "487.80",
                    "special_unit_price": null,
                    "special_total_price": null
                }
            ],
            "discounts": [],
            "tax_amount": "112.20",
            "total_price": "600.00",
            "discount_amount": "0.00",
            "base_total_price": "600.00",
            "total_netto_price": "487.80",
            "validation_status": "valid",
            "available_gratis_rules": null
        },
        "total": "600.00",
        "comment": null,
        "addresses": {
            "billing_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "tax_id": "123456789",
                "company": "Example Co",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48",
                "requested_invoice": false
            },
            "shipping_address": {
                "city": "Warsaw",
                "email": "customer@example.com",
                "street": "test street 89",
                "company": "",
                "lastname": "Testowicz",
                "postcode": "00-000",
                "firstname": "Test",
                "telephone": "500500500",
                "country_code": "PL",
                "dialling_code": "+48"
            },
            "validation_status": "valid"
        },
        "fee_price": "0",
        "total_tax": "112.20",
        "base_total": "609.99",
        "country_code": "PL",
        "currency_code": "PLN",
        "language_code": "pl",
        "payment_method": {
            "card": null,
            "code": "banktransfer",
            "name": "Przelew bankowy",
            "status": "valid",
            "bank_id": null,
            "continue_url": null,
            "country_code": "PL",
            "authorization_token": null,
            "is_cash_on_delivery": false
        },
        "custom_order_id": null,
        "shipping_method": {
            "code": "upsstandard",
            "name": "Kurier ups",
            "status": "valid",
            "tax_rate": "0.00",
            "unit_price": "9.99",
            "total_price": "0",
            "country_code": "PL",
            "delivery_point": null,
            "base_unit_price": "9.99",
            "discount_amount": "9.99",
            "base_total_price": "9.99",
            "free_delivery_above": "200.00",
            "cash_on_delivery_fee": "2.00"
        },
        "validation_status": "valid",
        "requested_delivery_date": null,
        "total_to_min_order_price": null,
        "id": "1000000004",
        "status": "unpaid",
        "status_label": null,
        "attachments": [],
        "shipping_intent":
        {
            "method_code": "kurier",
            "tracking_number": "123123123",
            "tracking_link": "https://tracking.example.com/track=123123123"
        },
        "invoices":
        [
            {
                "invoice_id": "2f4bfdd0-8e8f-4ff3-a22a-144b8d7e9620",
                "invoice_number": "ABC",
                "invoice_base64": "JVBERi0xLjEKJcKlwrHDqwoKMSAwIG9iagogIDw8IC9UeXBlIC9DYXRhbG9nCiAgICAgL1BhZ2VzIDIgMCBSCiAgPj4KZW5kb2JqCgoyIDAgb2JqCiAgPDwgL1R5cGUgL1BhZ2VzCiAgICAgL0tpZHMgWzMgMCBSXQogICAgIC9Db3VudCAxCiAgICAgL01lZGlhQm94IFswIDAgMzAwIDE0NF0KICA+PgplbmRvYmoKCjMgMCBvYmoKICA8PCAgL1R5cGUgL1BhZ2UKICAgICAgL1BhcmVudCAyIDAgUgogICAgICAvUmVzb3VyY2VzCiAgICAgICA8PCAvRm9udAogICAgICAgICAgIDw8IC9GMQogICAgICAgICAgICAgICA8PCAvVHlwZSAvRm9udAogICAgICAgICAgICAgICAgICAvU3VidHlwZSAvVHlwZTEKICAgICAgICAgICAgICAgICAgL0Jhc2VGb250IC9UaW1lcy1Sb21hbgogICAgICAgICAgICAgICA+PgogICAgICAgICAgID4+CiAgICAgICA+PgogICAgICAvQ29udGVudHMgNCAwIFIKICA+PgplbmRvYmoKCjQgMCBvYmoKICA8PCAvTGVuZ3RoIDU1ID4+CnN0cmVhbQogIEJUCiAgICAvRjEgMTggVGYKICAgIDAgMCBUZAogICAgKEhlbGxvIFdvcmxkKSBUagogIEVUCmVuZHN0cmVhbQplbmRvYmoKCnhyZWYKMCA1CjAwMDAwMDAwMDAgNjU1MzUgZiAKMDAwMDAwMDAxOCAwMDAwMCBuIAowMDAwMDAwMDc3IDAwMDAwIG4gCjAwMDAwMDAxNzggMDAwMDAgbiAKMDAwMDAwMDQ1NyAwMDAwMCBuIAp0cmFpbGVyCiAgPDwgIC9Sb290IDEgMCBSCiAgICAgIC9TaXplIDUKICA+PgpzdGFydHhyZWYKNTY1CiUlRU9GCg=="
            }
        ],
        "created": "2023-08-09 10:54",
        "updated": "2023-08-09 10:54"
    }
}
```

Przykładowe zastosowanie:
```bash
{{<base_url>}}/checkout/v1/<channel_idx>/orders/1000000004/
```

## GET Listing Order

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/orders/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/orders/
```

Zwraca zamówienia, wymaga autoryzacji tokenem typu access.  W przypadku gdy użytkownik przypisany do zamówienia nie będzie miał do niego uprawnień zostanie zwrócony komunikat FORBBIDEN z kodem 403

>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": "",
        "messages": [],
        "regional": {
            "language": null,
            "currency": null,
            "country": null
        }
    },
    "data": [
        {
            "cart": {
                "items": [
                    {
                        "sku": "MAX-36-Czarny",
                        "name": "MAX-36-Czarny",
                        "status": "valid",
                        "quantity": "6",
                        "tax_rate": "0.23",
                        "is_gratis": false,
                        "unit_price": "149.00",
                        "total_price": "894.00",
                        "base_unit_price": "590.00",
                        "discount_amount": "0.00",
                        "special_percent": 75,
                        "special_to_date": "2029-12-13",
                        "unit_tax_amount": "27.86",
                        "base_total_price": "3540.00",
                        "discount_percent": null,
                        "total_tax_amount": "167.16",
                        "unit_price_netto": "121.14",
                        "special_from_date": "2019-12-13",
                        "total_price_netto": "726.84",
                        "special_unit_price": "149.00",
                        "special_total_price": "894.00"
                    }
                ],
                "discounts": [],
                "tax_amount": "167.16",
                "total_price": "894.00",
                "discount_amount": "0.00",
                "base_total_price": "3540.00",
                "total_netto_price": "726.84",
                "validation_status": "valid",
                "available_gratis_rules": null
            },
            "total": "894.00",
            "comment": null,
            "addresses": {
                "billing_address": {
                    "city": "Warsaw",
                    "email": "customer@example.com",
                    "street": "test street 89",
                    "tax_id": "123456789",
                    "company": "Example Co",
                    "lastname": "Testowicz",
                    "postcode": "00-000",
                    "firstname": "Test",
                    "telephone": "500500500",
                    "country_code": "PL",
                    "dialling_code": "+48",
                    "requested_invoice": false
                },
                "shipping_address": {
                    "city": "Warsaw",
                    "email": "customer@example.com",
                    "street": "test street 89",
                    "company": "",
                    "lastname": "Testowicz",
                    "postcode": "00-000",
                    "firstname": "Test",
                    "telephone": "500500500",
                    "country_code": "PL",
                    "dialling_code": "+48"
                },
                "validation_status": "valid"
            },
            "fee_price": "0",
            "total_tax": "167.16",
            "base_total": "3549.99",
            "country_code": "PL",
            "currency_code": "PLN",
            "language_code": "pl",
            "payment_method": {
                "card": null,
                "code": "banktransfer",
                "name": "Przelew bankowy",
                "status": "valid",
                "bank_id": null,
                "continue_url": null,
                "country_code": "PL",
                "authorization_token": null,
                "is_cash_on_delivery": false
            },
            "custom_order_id": null,
            "shipping_method": {
                "code": "upsstandard",
                "name": "Kurier ups",
                "status": "valid",
                "tax_rate": "0.00",
                "unit_price": "9.99",
                "total_price": "0",
                "country_code": "PL",
                "delivery_point": null,
                "base_unit_price": "9.99",
                "discount_amount": "9.99",
                "base_total_price": "9.99",
                "free_delivery_above": "200.00",
                "cash_on_delivery_fee": "2.00"
            },
            "validation_status": "valid",
            "requested_delivery_date": null,
            "total_to_min_order_price": null,
            "id": "1000000002",
            "status": "unpaid",
            "status_label": null,
            "attachments": [],
            "shipping_intent": null,
            "invoices": [],
            "created": "2023-08-09 10:11",
            "updated": "2023-08-09 10:11"
        },
        {
            "cart": {
                "items": [
                    {
                        "sku": "MAX-36-Czarny",
                        "name": "MAX-36-Czarny",
                        "status": "valid",
                        "quantity": "6",
                        "tax_rate": "0.23",
                        "is_gratis": false,
                        "unit_price": "149.00",
                        "total_price": "894.00",
                        "base_unit_price": "590.00",
                        "discount_amount": "0.00",
                        "special_percent": 75,
                        "special_to_date": "2029-12-13",
                        "unit_tax_amount": "27.86",
                        "base_total_price": "3540.00",
                        "discount_percent": null,
                        "total_tax_amount": "167.16",
                        "unit_price_netto": "121.14",
                        "special_from_date": "2019-12-13",
                        "total_price_netto": "726.84",
                        "special_unit_price": "149.00",
                        "special_total_price": "894.00"
                    }
                ],
                "discounts": [],
                "tax_amount": "167.16",
                "total_price": "894.00",
                "discount_amount": "0.00",
                "base_total_price": "3540.00",
                "total_netto_price": "726.84",
                "validation_status": "valid",
                "available_gratis_rules": null
            },
            "total": "894.00",
            "comment": null,
            "addresses": {
                "billing_address": {
                    "city": "Warsaw",
                    "email": "customer@example.com",
                    "street": "test street 89",
                    "tax_id": "123456789",
                    "company": "Example Co",
                    "lastname": "Testowicz",
                    "postcode": "00-000",
                    "firstname": "Test",
                    "telephone": "500500500",
                    "country_code": "PL",
                    "dialling_code": "+48",
                    "requested_invoice": false
                },
                "shipping_address": {
                    "city": "Warsaw",
                    "email": "customer@example.com",
                    "street": "test street 89",
                    "company": "",
                    "lastname": "Testowicz",
                    "postcode": "00-000",
                    "firstname": "Test",
                    "telephone": "500500500",
                    "country_code": "PL",
                    "dialling_code": "+48"
                },
                "validation_status": "valid"
            },
            "fee_price": "0",
            "total_tax": "167.16",
            "base_total": "3549.99",
            "country_code": "PL",
            "currency_code": "PLN",
            "language_code": "pl",
            "payment_method": {
                "card": null,
                "code": "banktransfer",
                "name": "Przelew bankowy",
                "status": "valid",
                "bank_id": null,
                "continue_url": null,
                "country_code": "PL",
                "authorization_token": null,
                "is_cash_on_delivery": false
            },
            "custom_order_id": null,
            "shipping_method": {
                "code": "upsstandard",
                "name": "Kurier ups",
                "status": "valid",
                "tax_rate": "0.00",
                "unit_price": "9.99",
                "total_price": "0",
                "country_code": "PL",
                "delivery_point": null,
                "base_unit_price": "9.99",
                "discount_amount": "9.99",
                "base_total_price": "9.99",
                "free_delivery_above": "200.00",
                "cash_on_delivery_fee": "2.00"
            },
            "validation_status": "valid",
            "requested_delivery_date": null,
            "total_to_min_order_price": null,
            "id": "1000000001",
            "status": "unpaid",
            "status_label": null,
            "attachments": [],
            "shipping_intent": null,
            "invoices": [],
            "created": "2023-08-09 10:11",
            "updated": "2023-08-09 10:11"
        }
    ],
    "pagination": {
        "page": 1,
        "limit": 10,
        "pages": 1,
        "records": 2
    }
}
```
Parameters:

| Provider     | Description                               | Możliwe wartości                                               | Optional | Example                                    |
|--------------|-------------------------------------------|----------------------------------------------------------------|----------|--------------------------------------------|
| sort         | Służący sortowaniu                        | {"field": "created", "order": "ASC lub DESC"} (domyślnie DESC) | +        | &sort={"field": "created", "order": "ASC"} |
| order_status | Filtrowanie po statusie zamówienia        | new,confirmed,holded,in_progress,complete,returned,canceled    | +        | &order_status=new                          |
| order_id     | Filtrowanie po pretty id zamówienia       | -                                                              | +        | &order_id=1000000003                       |
| product_name | Filtrowanie po nazwie produktu zamówienia | -                                                              | +        | &product_name=Neque                        |

Przykładowe zastosowanie parametrów:
```bash
{{<base_url>}}/checkout/v1/<channel_idx>/orders/?sort={"field": "created", "order": "ASC"}&order_status=new&order_id=1000000003&product_name=Neque
```

## GET Order Attachment

Pozwala na pobranie załącznika (pliku) ordera

"RESPONSE ON SUCCESS"

```headers
Content-Type: application/octet-stream
Content-Disposition: attachment; filename=<file_name>
x-filename: <file_name>
Access-Control-Expose-Headers: x-filename
```

Plik jest w body odpowiedzi, przeglądarka powinna rozpoznać to jako pobranie pliku. Nie potrzebny jest api-key.

Adres:
`[PUBLIC_API_URL]/checkout/v1/orders/<str:order_id>/file/<str:file_id>/customer/<str:uid>`

Przykładowe zapytania:  
```
curl --location '[PUBLIC_API_URL]/checkout/v1/orders/6fb41a03-c1f9-414b-8dee-0c394f5981a2/file/6/customer/6fb41a03-c1f9-414b-8dee-0c394f5981a2'
```

## GET Countries

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/countries/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/countries/
```

Zwraca wszystkie zdefiniowane dla channel'a kraje, wraz z krajem domyślnym.
Możliwe jest odpytanie endpointu z parametrem languages.
Parametr ten zwraca nazwy krajów w danym języku, jeżeli jest on dostępny w ustawieniach channel'a. \
Na ten moment jest dostępny jedynie en, po podaniu innych/ lub bez parametru zwraca w języku polskim


```bash
np. {{<base_url>}}/checkout/v1/<channel_idx>/countries/?language=en
```


>Przykładowa odpowiedź
```json
{
    "meta": {
        "status": "OK",
        "message": ""
    },
    "data": {
        "countries": [
            {
                "code": "PL",
                "label": "Poland",
                "prefix": "+48"
            },
            {
                "code": "MT",
                "label": "Malta",
                "prefix": "+356"
            },
            {
                "code": "NL",
                "label": "Netherlands",
                "prefix": "+31"
            },
            {
                "code": "NO",
                "label": "Norway",
                "prefix": "+47"
            }
        ],
        "default_country": {
            "code": "PL",
            "label": "Poland",
            "prefix": "+48"
        }
    }
}
```

## API surface

### v1 (function-based views, marshmallow DTOs)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout/1/{channel}/carts/` | Create cart |
| GET | `/api/checkout/1/{channel}/customer/cart/` | Latest customer cart |
| GET/PATCH | `/api/checkout/1/{channel}/carts/{id}/` | Get/update cart |
| GET | `/api/checkout/1/{channel}/carts/{id}/shipping-methods/` | Available shipping |
| GET | `/api/checkout/1/{channel}/carts/{id}/payment-methods/` | Available payment |
| GET | `/api/checkout/1/{channel}/carts/{id}/gratis-rules/` | Free product rules |
| POST | `/api/checkout/1/{channel}/carts/{id}/merge/` | Merge guest cart |
| POST/GET | `/api/checkout/1/{channel}/orders/` | Create/list orders |
| GET | `/api/checkout/1/{channel}/orders/{id}/` | Order detail |
| POST | `/api/checkout/1/{channel}/notify/{provider}/` | Payment webhooks |

### v2 (DRF APIViews, Pydantic schemas — in progress)

Sub-resource PATCH pattern. Each PATCH sends one concern, returns full cart.

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/checkout/v2/{channel}/carts/` | Create cart |
| GET | `/api/checkout/v2/{channel}/carts/{id}/` | Cart detail |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/items/` | Update items |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/addresses/` | Set addresses |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/discounts/` | Apply discount codes |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/shipping/` | Select shipping |
| PATCH | `/api/checkout/v2/{channel}/carts/{id}/payment/` | Select payment |
| POST | `/api/checkout/v2/{channel}/orders/` | Create order |
| GET | `/api/checkout/v2/{channel}/orders/` | List orders |

v2 price fields use explicit gross/net naming (aligned with Matrix v2).

### v2 payment return URL

`PATCH carts/{id}/payment/` takes an optional `continue_url`: where the gateway sends the customer back after
paying, `<order_id>` replaced by the order number (`https://shop.example.org/checkout/success?ref=<order_id>`), at
most 2048 characters; surrounding spaces are dropped and an empty one means none. It is stored with the payment
method as v1's `payment_method.continue_url` is, and never returned. PayU (`payu`,
`payu_card`, `payu_blik`) and Stripe hosted checkout read it; Stripe opens no session without one. Przelewy24,
PayPal and Paynow return to the URLs in the method's `additional_data`.

The target must be one the payment step accepts, or the PATCH answers 400 (`VALIDATION_ERROR`, `field:
"continue_url"`) and changes nothing: a scheme in `PAYMENT_REDIRECT_DIRECT_SCHEMES` (default `https`) goes to the
gateway as it is, any other goes through the payment bridge and needs its scheme in
`PAYMENT_REDIRECT_BRIDGE_TARGET_SCHEMES` and, for http/https, its host in `PAYMENT_REDIRECT_ALLOWED_HOSTS`. v1
checks the same rule only when the order starts the payment.

### v2 cart warnings (`_errors`)

A cart write (create, PATCH items / addresses / discounts / shipping / payment, merge) answers 200/201 with the full
cart; what went wrong in it is listed under `_errors` (the key is absent when nothing did). Each detail is
`{scope, field, code, message, meta}`; branch on `code`, `message` is generic English text. Reads (GET) carry no
`_errors`. `POST orders/` refuses the order when a code the cart holds no longer applies (another order took its
last use, the address reached `max_uses_per_user`, it expired, ...): 409 with the code's reason from the table below
as `error` (`DISCOUNT_USAGE_LIMIT_REACHED`, ...) and the code in `message`. Nothing is ordered and the cart keeps the
code; the next discounts PATCH drops it and lists it under `_errors`, and the order then goes through at the new total.

Rejected discount codes give `ONE_OR_MORE_DISCOUNT_INVALID` (`field: "cart.discounts.code"`, as before) and then one
detail per rejected code. `field` is `codes[i].code`, `i` being the code's place among the codes sent (the
`codes` of a discounts PATCH, else the codes the cart already holds).

```json
"_errors": [
  {"scope": "discounts", "field": "cart.discounts.code", "code": "ONE_OR_MORE_DISCOUNT_INVALID",
   "message": "Some discounts are invalid", "meta": null},
  {"scope": "discounts", "field": "codes[1].code", "code": "DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE",
   "message": "Discount code is invalid.", "meta": {"code": "PERCENT10", "blocking_code": "PRIORITY5"}}
]
```

| `code` | When | `meta` besides `code` |
|---|---|---|
| `DISCOUNT_CODE_NOT_FOUND` | unknown code, inactive rule, another channel's code, or a rule that excludes this customer / cart quantity | — |
| `DISCOUNT_EXPIRED` | code's `active_to` passed | `expired_at` (ISO date) |
| `DISCOUNT_NOT_YET_ACTIVE` | code's `active_from` ahead | `active_from` (ISO date) |
| `DISCOUNT_USAGE_LIMIT_REACHED` | `current_used` reached `max_used` | — |
| `DISCOUNT_PER_USER_LIMIT_REACHED` | this email used it `max_uses_per_user` times | — |
| `DISCOUNT_MIN_ORDER_NOT_MET` | cart total below the rule's `min_order_amount` | `min_order_amount` |
| `DISCOUNT_LOGIN_REQUIRED` | first-order-for-logged-in rule, guest cart | — |
| `DISCOUNT_FIRST_ORDER_ONLY` | first-order rule, the email has orders | — |
| `DISCOUNT_CURRENCY_NOT_SUPPORTED` | the rule has no value for the cart currency, or its `currencies` leave the cart currency out | `currency` |
| `DISCOUNT_DUPLICATE_RULE` | another code of the same rule (or the same code) is already in the cart | — |
| `DISCOUNT_BLOCKED_BY_EXCLUSIVE_RULE` | a higher-priority rule won arbitration (it or this code is exclusive) | `blocking_code`, only when the client already sees it: a code it sent or a discount applied in this cart |
| `DISCOUNT_BLOCKED_BY_PRICE_RULES` | the price list allows no discount codes | — |
| `DISCOUNT_CODE_INVALID` | rejected while the discount was calculated (no eligible products, gratis checks) | — |

The meta never carries rule internals (priority, ids, usage counters). v1 responses are unchanged: v1 still gets
only `one_or_more_discount_invalid` with `affected_values`.

### Code usage

An order, v1 or v2, counts each code it took on `current_used` and writes one `UsedCoupon` row per code with the
order's e-mail addresses, in the order's transaction. `max_used` is checked against the counter. `max_uses_per_user`
is checked against the `UsedCoupon` rows of orders in `ORDER_STATUSES_QUALIFIED_TO_DISCOUNT_MAX_PER_USER` (default:
completed, returned, canceled), matched by e-mail: the account e-mail of a logged-in customer, the address e-mail of
a guest cart. A cancelled order keeps its use. Of two orders racing for the last use one takes it and the other is
not saved (v2 409 `DISCOUNT_USAGE_LIMIT_REACHED`, v1 403 `INVALID_REQUEST`). An automatic rule applies whatever its
code's counter says, so its code counts up to `max_used` and then stops counting; it never refuses an order. A v1
order whose code no longer applies is still placed without it.

The use is taken after the order is written and its payment started, so the code row is locked for a moment and
never during a call to a payment gateway. The cost falls on the order that loses the race: with a gateway method
(PayU, PayPal, Przelewy24, Paynow, Stripe, Autopay) the gateway has already opened a payment session for it when it
rolls back. The customer never gets that session's redirect URL, so no money moves; the gateway keeps it as an
abandoned, unpaid session. Nothing of it is left in the shop: the order and its payment intent roll back together,
and a notification the gateway may still send for that session finds no payment and is logged as an unknown one.

### Stored gateway payloads

`PaymentIntent.provider_request` (what checkout sent to the gateway) and `provider_notify` (what the gateway
answered or notified) are stored through an allowlist (`utils/gateway_payload.py`), for every provider and on
every save: transaction and order ids, amounts, currencies, statuses and timestamps. The buyer (name, e-mail,
phone, address, IP, PayPal payer and payer id, Stripe `customer_details`), descriptions, URLs, product lists,
signatures and payment credentials are never stored. Signatures are verified on the raw request before anything
is stored, and a notification that arrives after an erasure is stored the same way, so it brings no personal data
back. The PayU, PayPal, Stripe and Autopay notification views log and send to BI a payload only through the same
scrub; Paynow and Przelewy24 notifications carry no buyer data. A payment stored before this release keeps its
whole payloads until it is saved again (a later notification, an edit in the Django admin) or the customer is
erased. `manage.py scrub-gateway-payloads` reports how many still hold more (dry run) and reduces them with
`--apply`.

### Customer erasure

`DELETE /api-admin/checkout/1/{channel}/customer/delete` with `{"email": "<address>"}` (admin key, scope
`checkout.erase`; `django_checkout.output.admin.admin_customer_delete` is the same for Python callers) anonymises
the orders and carts of that e-mail. It finds the customer's orders by the account, the `billing_email` /
`shipping_email` columns, the e-mail of the billing or shipping address in `order_body` and the e-mail a used code
stored, so a repeated request completes an order an earlier one left half done; carts by the account and the
address e-mails in `cart_body`. Every match is the whole address, case aside: erasing `anna@example.org` leaves
`joanna@example.org` alone. A pinned erase token covers its channel only.

Another customer's order or cart that holds the e-mail in some other field keeps everything but that e-mail: in
every string of the body (a gift card's `recipient_email` in `cart.items[].voucher_gift`, the `comment`, any other
field) the address becomes the marker, and the rest of the text, the names, the addresses, the e-mail columns and
the payments stay. The response lists such an order or cart with the others. It never refuses the erasure, even
while the shop still has to fulfil it: a gift card bought for the erased person then has no address to go to.

| Where | Anonymised | Kept |
|---|---|---|
| `order_body` / `cart_body` addresses | first and last name, e-mail, street, city, postcode, phone, company, tax id | country, dialling code, invoice flags, `address_id`, `external_id` |
| `order_body` / `cart_body` | `comment`, `customer_ip`, gift-card personalisation, payment credentials (`***REDACTED***`, masked voucher codes stay) | totals, items, codes, shipping method and pickup point, `affiliate_code` |
| `Order` | `billing_email`, `shipping_email` | status, numbers, `customer` (the account erasure in `django_accounts` sets it to NULL) |
| `UsedCoupon` | `billing_email`, `shipping_email` | the code and the order (`max_uses_per_user` no longer ties the use to the erased e-mail) |
| `PaymentIntent` | `provider_request`, `provider_notify` stored before payloads went through the allowlist: everything except ids, amounts, currencies, statuses and timestamps; `redirect_url` (`***REDACTED***`: the gateway's payment page or, for BLIK, a copy of the body's `continue_url`; only the order's creation answer hands it out) | `amount`, `currency`, `external_order_id`, `payment_status`, `code` |
| `PaymentRedirect` | `target_url` (`***REDACTED***`: a copy of the body's `continue_url`) | token, expiry, consumption time |
| `OrderMail` | `recipient` (blank) | kind, status, attempts, last error, send time |

Names and free text become `x` of the same length, e-mails `x…x@example.com`. Not touched: invoices
(`Invoice.invoice_base64`, kept for accounting), order attachments, shipping tracking numbers, `Order.extra`
(integrator data).

#### Orders not fulfilled yet refuse it

While the customer has an order the shop still has to fulfil, the erasure is refused and nothing is anonymised:
not that order, not the customer's other orders, not the carts. Another customer's order that only holds the
e-mail never refuses it. A pinned token looks at its channel's orders only.

| Status | Refuses the erasure | Why |
|---|---|---|
| `new`, `unpaid` | yes | waiting for payment; a gateway confirms the payment for days after the order, and the shop then ships it |
| `confirmed` | yes | paid, not shipped |
| `holded` | yes | on hold; it goes back to `confirmed` or is cancelled |
| `in_progress` | yes | being prepared, not handed to the carrier yet |
| `complete` | no | delivered; the tracking number stays for complaints |
| `returned`, `canceled` | no | nothing left to ship |

v1 answers 409 with the blocking orders' ids, numbers and statuses, never the e-mail or any other personal value:

```json
{"meta": {"status": "OPEN_ORDERS", "message": "The customer has orders that are not fulfilled yet; nothing was anonymized. Cancel or finish the listed orders, then erase again", ...},
 "data": {"deleted": false, "blocking_orders": [{"order_id": "<uuid>", "pretty_id": "0100000042", "status": "confirmed"}]}}
```

The Python API raises `django_checkout.output.admin.ErasureBlocked` instead; its `orders` holds the same
`BlockingOrder(order_id, pretty_id, status)` entries.

To erase the customer, cancel or finish (ship and complete) every listed order, then send the request again.
Nothing cancels an order by itself: an abandoned order that waits for payment stays `unpaid` and refuses every
erasure until someone cancels it, in the CMS or with `POST /api/checkout/v2/admin/{channel}/orders/{uid}/cancel/`. One
gap stays: a cancelled order whose payment still arrives is confirmed again (logged CRITICAL for manual review,
`notification_state.confirm_order`); erased in between, it has no address left to ship to.

#### Erasure order: checkout first, then the account

Erase in checkout first, then delete the account in `django_accounts` (`accounts.erase`). Send the account e-mail and
every address e-mail the customer used in checkout: each request finds the orders and carts of the e-mail it names.

The other way round, deleting the account sets `customer` to NULL on the orders, carts and used codes, and checkout
can no longer find anything through the account. It then finds an order only by the e-mail sent: in the addresses of
`order_body`, in the `billing_email` / `shipping_email` columns, or in a `UsedCoupon` row, which keeps the account
e-mail of a logged-in customer's code. An order placed with another address e-mail and no code is reachable only by
that address e-mail; a cart only by the e-mail in its addresses. Orders and carts with no e-mail of the customer keep
no personal data once the account is gone.

#### Orders erased before

Orders erased by an earlier release kept the other fields: `manage.py complete-erasures` reports them (rows whose
address e-mail is anonymised), `--apply` finishes them, and `--marker-domain <domain>` adds the domain an earlier
release line anonymised e-mails to, for a database that came from one. The command finishes erasures already made,
so orders not fulfilled yet do not stop it.

## Order e-mails

With the extra `email` (`entirius-django-email`) installed, every purchase gets one confirmation e-mail and a
confirmed order that is cancelled gets one cancellation e-mail. Both are sent by the Celery task
`django_checkout.send_order_mail` (default queue `celery`) and logged in `OrderMail`, one row per order and kind.

| E-mail | Sent when |
|---|---|
| Confirmation | at placement when the method that settles the order is not an online gateway (transfer, cash on delivery, cash, deferred payment, free order, or fully covered by a voucher); at the first `confirmed` transition for a gateway (PayU, PayPal, Paynow, Przelewy24, Autopay, Stripe). An abandoned online payment sends nothing |
| Cancellation | at the transition to `canceled`, only when the purchase's confirmation is `sent` or `pending`; the refund note when the order was paid (status `confirmed`, `holded`, `in_progress` or `complete` before, and not cash on delivery) |

A split purchase gets one confirmation listing every part with its own number, totals summed over the parts; its row
lives on the lead order (the part that processed the payment). A cancellation names the cancelled part only.

Recipient: `billing_email`, else `shipping_email`; neither → the row is `skipped`. Language: `language_code` of the
order body when the e-mail module offers it, else the channel's default language. The e-mail channel is the
`django_email` channel with the checkout channel's idx; a shop without one → `skipped`.

The task locks its row, so one row is sent once. SMTP and socket errors are retried after 60, 180, 420, 900 and
1800 s; after the last one the row is `failed` and, with `django_notifications` installed, staff get one
notification ("Order e-mail not sent", role `orders`, subject `checkout.Order:<order uuid>`). Any other error (a
broken template) fails the row at once, without a retry. `last_error` holds the exception class and a short message,
never the address or the body.

### Admin API

`GET /api/checkout/v2/admin/{channel}/orders/{uid}/` lists the order's e-mails in `mails`: its own rows plus, for a
part of a split purchase, the lead's confirmation.

```json
"mails": [{"kind": "confirmation", "status": "sent", "recipient": "buyer@example.com", "language": "en",
           "attempts": 1, "last_error": "", "sent_at": "2026-11-12T15:58:01Z", "created": "2026-11-12T15:58:00Z",
           "order_numbers": ["0200000001", "0200000002"]}]
```

`POST /api/checkout/v2/admin/{channel}/orders/{uid}/mails/{kind}/resend/` (`kind`: `confirmation` | `canceled`)
puts the row back to `pending`, records who asked (`requested_by`) and queues a send: 202 with the row. A
confirmation of a split part resends the lead's. 400 `VALIDATION_ERROR` when the order has no e-mail address, or
for `canceled` when no cancellation e-mail was ever sent (a resend never creates one); 404 for another kind. Needs
write on `checkout.orders`; throttled per user, scope `checkout_order_mail_resend` (fallback `30/hour`).

Known limits: the status history is not stored, so a resent cancellation carries the refund note only for an online
gateway order whose confirmation was sent (a paid transfer order loses it on a resend). A split purchase whose lead
was cancelled and then paid late keeps the lead `canceled` (`Order.save`) and gets no confirmation; resend it.
A confirmation still retrying when its order is cancelled is `skipped`; the cancellation e-mail tells the customer.
A task the broker refuses leaves its row `failed` (`last_error` `…: not enqueued`).

