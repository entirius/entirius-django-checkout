# Sygnały walidacji przedmiotów w Django Checkout

## Opis

Mechanizm sygnałów walidacji przedmiotów pozwala na rozszerzenie funkcjonalności walidacji przedmiotów w koszyku bez modyfikacji kodu głównego modułu django-checkout.

## Konfiguracja

Aby włączyć mechanizm sygnałów walidacji przedmiotów, należy w ustawieniach projektu dodać:

```python
# settings.py
USE_VALIDATE_ITEMS_SIGNAL = True
```

## Tworzenie własnego odbiornika sygnału

Aby stworzyć własny mechanizm walidacji przedmiotów, należy utworzyć odbiornik sygnału `validate_items_signal`:

```python
from django.dispatch import receiver
from django_checkout.domain.validators.cart import validate_items_signal
from django_utils.api.responses import ErrorInfo

@receiver(validate_items_signal)
def validate_my_items(sender, items, **kwargs):
    """
    Niestandardowa walidacja przedmiotów
    
    Args:
        sender: Nadawca sygnału
        items: Lista przedmiotów do walidacji
        
    Returns:
        Tuple (items_not_allowed, msg_items):
        - items_not_allowed: Lista SKU niedozwolonych przedmiotów
        - msg_items: Lista komunikatów ErrorInfo
    """
    items_not_allowed = []
    msg_items = []
    
    for item in items:
        # Przykładowa logika walidacji - ograniczenie przedmiotów z prefiksem 'TEST-'
        if item.sku.startswith('TEST-'):
            items_not_allowed.append(item.sku)
            msg_items.append(
                ErrorInfo(
                    code="test_item_not_allowed",
                    message="Przedmioty testowe nie są dozwolone",
                    affected_values=[item.sku],
                    affected_field="cart.items.sku",
                )
            )
    
    return items_not_allowed, msg_items
```

## Proces walidacji

1. Gdy włączony jest mechanizm sygnałów walidacji (`USE_VALIDATE_ITEMS_SIGNAL = True`), podczas walidacji koszyka wysyłany jest sygnał `validate_items_signal`.
2. Wszystkie zarejestrowane odbiorniki sygnału są wywoływane z parametrem `items` zawierającym listę przedmiotów do walidacji.
3. Każdy odbiornik zwraca krotę `(items_not_allowed, msg_items)` z listą SKU niedozwolonych przedmiotów i listą komunikatów błędów.
4. Przedmioty, których SKU znajdują się na liście `items_not_allowed`, są usuwane z koszyka.
5. Komunikaty błędów z `msg_items` są dodawane do listy komunikatów błędów zwracanych przez proces walidacji.

## Uwagi

- Mechanizm działa na samym początku procesu walidacji, przed sprawdzaniem dostępności produktów, cen, itp.
- Można zarejestrować wiele odbiorników sygnału, wszystkie zostaną wywołane podczas walidacji.
- Przedmioty są usuwane z koszyka tylko wtedy, gdy odbiornik sygnału zwróci ich SKU na liście `items_not_allowed`. 