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
  - `"webhook_id"`: the webhook id from the PayPal developer dashboard (the REST app -> Webhooks -> Webhook ID),
    same sandbox/live environment as `client_id`. Without it `notify/paypal/` refuses every notification (403);
    see [POST Webhook (notify)](#post-webhook-notify).

```json
{"client_id": "<client-id-from-paypal-panel>", "use_sandbox": true, "client_secret": "<client-secret-from-paypal-panel>", "webhook_id": "<webhook-id-from-paypal-panel>", "return_url":  "... link do strony frontu, która strzeli z parametrami do naszego backendowego endpointu w przypadku potwierdzenia płatności...", "cancel_url": "... link do strony frontu, która strzeli z parametrami do naszego backendowego endpointu w przypadku anulowania płatności..."}
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

## POST Webhook (notify)

```bash
# Address to register as the webhook URL in the PayPal dashboard:
{{<base_url>}}/checkout/v1/<channel_idx>/notify/paypal/
```

PayPal posts payment events here (for example `PAYMENT.CAPTURE.COMPLETED`, `CHECKOUT.ORDER.APPROVED`). The endpoint
changes the payment and the order only when:

- the PayPal order id in the event belongs to a payment whose payment_method has the PayPal provider (an event for a
  PayU, Stripe or other payment is answered 403),
- that payment_method has `webhook_id` in additional_data,
- PayPal confirms the signature: `POST /v1/notifications/verify-webhook-signature` with the `PAYPAL-*` transmission
  headers, the `webhook_id` and the event as received, on that payment_method's credentials (10 s timeout).

When the payment exists but any of these fails, the answer is 403 and nothing is written. PayPal redelivers a
notification answered with anything but 2xx (for about 3 days), so webhooks refused before `webhook_id` is set land
once it is. `manage.py check --database default` lists PayPal methods without it (`django_checkout.W002`).

A body that cannot be processed (not JSON, no `resource.id`, no `supplementary_data` on `PAYMENT.CAPTURE.COMPLETED`,
an unknown payment) gets 200 with `status: FAIL` and writes nothing either; PayPal does not redeliver those.

PayPal sends every event of an app to every webhook of that app. Several channels on one PayPal app (the same
`client_id`): one webhook, and the same `webhook_id` in all their payment_methods.







