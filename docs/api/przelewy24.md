# Przelewy24 SDK

## Konfiguracja

Aby skorzystać z systemu płatnośći Przelewy24 należy dodać payment_method z poziomu admina o typie providera Przelewy24.
Minimalne dane to:

- Co najmniej 1 kraj aby podczas pobierania danych w koszyku można było uzyskać tę płatność
- Nazwa wyświetlana w JSON np:

```json
{
  "en": "Przelewy24",
  "pl": "Przelewy24"
}
```

- Dane dostępowe do Przelewy24 w additional_data np:
  - Uwaga: nie podanie `"use_sandbox"` skutkuje zawsze uzywaniem sandboxa.
  - Domyślnie regulation_accept jest ustawione na `false` i wymaga akceptacji regulaminu przez klienta.
  - `"crc_key"`: the CRC key of the POS (Przelewy24 panel); without it the `PRZELEWY24_CRC_KEY` setting is used.
    Notifications are verified with it: with neither, every notification is refused (403).

```json
{
  "pos_id": "11111",
  "client_id": "111111",
  "use_sandbox": true,
  "client_secret": "0000000000000",
  "regulation_accept": false,
}
```

## Usage

Aby utworzyć nową transakcję, użyj metody `create_transaction` z obiektem `TransactionRequest`.
Podczas tworzenia koszyka należy podać kod utworzonej payment_method, która jest przypisana do proviedera Przelewy24.
Zgodnie z dokumentacją `checkout.md`

```bash
# Adres:  
{{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
# Przykładowe zapytanie:
curl -X PATCH {{<base_url>}}/checkout/v1/<channel_idx>/carts/<cart_id>/
```

> Przykładowa treść zapytania `payment method`

```json
{
  "payment_method": {
    "code": "przelewy24",
    "continue_url": "https://url.do.ktorego.wrocisz.po.platnosci"
  }
}
```

Gdy utworzymy transakcję w odpowiedzi zwrotnej otrzymamy `token` - i zostaniemy przekierowani do panelu
transakcyjnego: https://secure.przelewy24.pl/trnRequest/{`token`} Przelewy24.
Zostanie otwarty w przeglądarce uruchomi system płatności Przelewy24. Po poprawnym wybraniu płatności, strona
przekieruje na nasz
`urlStatus` gdzie zostatnie dokończony proces płatności.

Po opłaceniu transakcji, Przelewy24 wyśle notyfikację o statusie transakcji. Skonfiguruj odpowiedni endpoint w swojej
aplikacji do obsługi notyfikacji.

- Testowe konta sandboxowe przelewy24: https://sandbox.przelewy24.pl/panel/

## POST Notification (urlStatus)

```bash
# Address Przelewy24 posts the transaction notification to (sent as urlStatus when the transaction is registered):
{{<base_url>}}/checkout/v1/<channel_idx>/notify/przelewy24/
```

The endpoint completes the payment only when:

- the `sessionId` belongs to a payment whose payment_method has the Przelewy24 provider (another gateway's payment is
  answered 403),
- the notification's `sign` matches: SHA-384 of `{"merchantId","posId","sessionId","amount","originAmount",
  "currency","orderId","methodId","statement","crc"}` as compact JSON with non-ASCII characters as they are, `crc`
  being the method's `crc_key` (403 otherwise, Przelewy24 is not asked),
- Przelewy24's verify API (`PUT /transaction/verify`, on the method's credentials) confirms the transaction.

A confirmed payment completes the order only while it waits for payment; a resent notification of a paid
transaction changes nothing.

