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