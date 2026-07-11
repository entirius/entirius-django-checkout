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

