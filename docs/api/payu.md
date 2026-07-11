# PayU

## Konfiguracja

Aby skorzystać z systemu płatnośći PayU należy dodać payment_method z poziomu admina o typie providera PayU lub PayUCard.
Minimalne dane to:
- Co najmniej 1 kraj aby podczas pobierania danych w koszyku można było uzyskać tę płatność
- Nazwa wyświetlana w JSON np: 

```json
{"en": "PayU", "pl": "PayU"}
```

- Dane dostępowe do PayU w additional_data np:
  - Uwaga: nie podanie `"use_sandbox"` skutkuje zawsze uzywaniem sandboxa.

```json
{"pos_id": "123456", "client_id": "123456", "use_sandbox": true, "client_secret": "<client-secret-from-payu-panel>"}
```

## Używanie

Podczas tworzenia koszyka należy podać kod utworzonej payment_method, która jest przypisana do proviedera PayU.
Zgodnie z dokumentacją `checkout.md`

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X PATCH {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

>Przykładowa treść zapytania `payment method`
```json
{
    "payment_method": {
        "code": "payu",
        "continue_url": "https://url.do.ktorego.wrocisz.po.platnosci",
        "card": "Tokenizowana karta" // Tylko w przypadku PayU Card
    }
}
```

Gdy utworzymy zamówienie w odpowiedzi zwrotnej otrzymamy `redirect_url` - 
otwarty w przeglądarce uruchomi system płatności PayU a jej finalizacja zapisze pałatność na koncie PayU.
W przypadku PayUCard redirect_url może kierować do systemu autoryzacji 3DS aby potwierdzić płatność kartą. 
Czasami może nie zwrócić redirect_url co oznacza że 3DS nie był wymagany. Wtedy wystarczy przerzucić usera od razu na THX page.
Po poprawnej płatności system PayU powinien odpowiedzieć notyfikacją pod przygotowany endpoint w volkanos django_checkout.
Strzał do tego endpoint w zależności od wysłanych danych potrafi zmienić status płatności i zamówienia.

- Testowe numery kart płatniczych można pobrać tutaj: https://developers.payu.com/en/overview.html#sandbox_cards
- System tokenizacji wraz z działającym przykładem znajduje się tutaj: https://developers.payu.com/pl/card_tokenization.html#secureform_example

Endpoint znajduje się pod adresem:
```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/notify/payu/
# Przykładowe zapytanie:
curl -X POST {{<base_url>}}/checkout/v1/<channel_idx>/notify/payu/
```

>Przykładowa treść zapytania `notify payu` z dokumentacji PayU potwierdzająca płatność
```json
{
  "order": {
    "orderId": "6NDJ8HZ7J6221103GUEST000P01", // ID zamówienia w systemie PayU - odłożone w tabeli payment_intent jako external_order_id
    "extOrderId": "Id zamówienia w Twoim sklepie",
    "orderCreateDate": "2012-12-31T12:00:00",
    "notifyUrl": "http://tempuri.org/notify",
    "customerIp": "127.0.0.1",
    "merchantPosId": "Id punktu płatności (pos_id)",
    "description": "Twój opis zamówienia",
    "currencyCode": "PLN",
    "totalAmount": "200",
    "buyer": {
      "email": "john.doe@example.org",
      "phone": "111111111",
      "firstName": "John",
      "lastName": "Doe",
      "language": "pl"
    },
    "payMethod": {
      "type": "PBL"
    },
    "products": [
      {
        "name": "Product 1",
        "unitPrice": "200",
        "quantity": "1"
      }
    ],
    "status": "COMPLETED"
  },
  "localReceiptDateTime": "2016-03-02T12:58:14.828+01:00",
  "properties": [
    {
      "name": "PAYMENT_ID",
      "value": "151471228"
    }
  ]
}
```

Cały notify zostatnie zapisany w bazie danych w tabeli payment_intnet w rekordzie przypisanym do zamówienia.

## Jak przetestować/ wykonać płatność nieudaną?

W przypadku PayUCard wystarczy podać nieprawidłowe dane karty. W przypadku PayU wystarczy podać nieprawidłowe dane do karty/bliku lub nie zatwierdzić płatności w systemie PayU.
Karte niepoprawną testową można pobrać tutaj: https://developers.payu.com/europe/pl/docs/testing/sandbox/#sandbox-test-cards.
Wszystkie zmiany statusów przychodzą na notify/payu/ i są zapisywane w tabeli payment_intent. Aby zasymulować zamknięcie płatności wystarczy wysłać request z danymi zamówienia i status "CANCELED" na endpoint notify/payu/, jak poniżej:

>Przykładowa treść zapytania `notify payu` z dokumentacji PayU anulujący
```json
{
  "order": {
        "orderId": "LDLW5N7MF4140324GUEST000P01", // ID zamówienia w systemie PayU - odłożone w tabeli payment_intent jako external_order_id
        "extOrderId": "Order id in your shop",
        "orderCreateDate": "2012-12-31T12:00:00",
        "notifyUrl": "http://tempuri.org/notify",
        "customerIp": "127.0.0.1",
        "merchantPosId": "{POS ID (pos_id)}",
        "description": "My order description",
        "currencyCode": "PLN",
        "totalAmount": "200",
        "products": [
            {
                "name": "Product 1",
                "unitPrice": "200",
                "quantity": "1"
            }
        ],
        "status": "CANCELED"
    }
}