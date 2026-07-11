# PayPal

## Konfiguracja

Aby skorzystać z systemu płatnośći PayPal należy dodać payment_method z poziomu admina o typie providera PayPal.
Minimalne dane to:
- Co najmniej 1 kraj aby podczas pobierania danych w koszyku można było uzyskać tę płatność
- Nazwa wyświetlana w JSON np: 

```json
{"en": "PayPal", "pl": "PayPal"}
```

- Dane dostępowe do PayPal w additional_data np:
  - Uwaga: nie podanie `"use_sandbox"` skutkuje zawsze uzywaniem sandboxa.

```json
{"client_id": "<client-id-from-paypal-panel>", "use_sandbox": true, "client_secret": "<client-secret-from-paypal-panel>", "return_url":  "... link do strony frontu, która strzeli z parametrami do naszego backendowego endpointu w przypadku potwierdzenia płatności...", "cancel_url": "... link do strony frontu, która strzeli z parametrami do naszego backendowego endpointu w przypadku anulowania płatności..."}
```

## Używanie

Podczas tworzenia koszyka należy podać kod utworzonej payment_method, która jest przypisana do proviedera PayPal.
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
        "code": "paypal"
    }
}
```

Gdy utworzymy zamówienie w odpowiedzi zwrotnej otrzymamy `redirect_url` - 
otwarty w przeglądarce uruchomi system płatności Paypal. Po poprawnym/niepoprawnym wybraniu płatności, strona przekieruje na nasz endpoint podanym w payment_method: 
"return_url" - jeżeli klient potwierdzi metodę płatności na stronie PayPal
"cancel_url" - jeżeli klient odrzuci metodę płatności na stronie PayPal


To przekierowanie posiada paramtery token i PayerID. Aby proces został dokończony frontend musi wywołać endpoint return/cancel (w zaleźności od przypadku) z paramterami podanymi przez PayPal/
Endpoint w zależności czy płatność została poprawnie sfinalizowana potrafi zmienić status płatności i zamówienia.
- Testowe konta sandboxowe paypal: https://developer.paypal.com/dashboard/accounts
- 
## GET Capture payment

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/return/paypal/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/return/paypal/
```

Pozwala zfinalizować płatność na podstawie tokenu podanego w parametrze.

>Przykładowa odpowiedź
```json

```

| Parameter | Description |
|-----------|-------------|
| token     | (str)       |
| PayerID   | (str)       |

## GET Cancel payment

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/cancel/paypal/
# Przykładowe zapytanie:
curl -X GET {{<base_url>}}/checkout/v1/<channel_idx>/cancel/paypal/
```

Pozwala anulować płatność na podstawie tokenu podanego w parametrze.

>Przykładowa odpowiedź
```json

```

| Parameter | Description |
|-----------|-------------|
| token     | (str)       |







