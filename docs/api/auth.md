# Auth

API jest dostępne za kluczem. Klucz należy umieścić w nagłówku zapytania jako 'X-API-KEY'.  

## Przykładowe zapytanie:

```bash
curl --location --request GET 'https://localhost/api/checkout/v1/<channel_idx>/cart/' \
--header 'x-api-key: 0000000000000000000000000000000000000000000000000000000000000000'
```

## Przykładowe odpowiedzi

```json
{
    "meta": {
        "status": "ERR",
        "message": "Unhandeld exception"
    },
    "data": {}
}
```
```json
{
    "meta": {
        "status": "BAD_REQUEST",
        "message": "Invalid request"
    },
    "data": {}
}
```
```json
{
    "meta": {
        "status": "NOT_FOUND",
        "message": "Resource not found"
    },
    "data": {}
}
```

Poniżej znajduje się lista zdefiniowanych błędów. 

| HTTP | STATUS             | MESSAGE                 |
|------|--------------------|-------------------------|
| 400  | BAD_REQUEST        | Invalid request         |
| 400  | EMAIL_TAKEN        | Email already in use    |
| 401  | UNAUTHORIZED       | Requires authentication |
| 403  | FORBIDDEN          | Permission denied       |
| 404  | NOT_FOUND          | Resource not found      |
| 405  | METHOD_NOT_ALLOWED | Invalid http method     |
| 500  | ERR                | Unhandeld exception     |
