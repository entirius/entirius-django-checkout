
Django Checkout
===============

## Do działania API potrzebne dane w django admin:

- wygenerowany APIKey
- wpis w Channel
- conajmniej jedna metoda shipping
- conajmniej jedna metoda payment
- product representation i stock produktu


# Koszyk

## Założenia

Aby nie przechowywac w bazie danych, które nic nie wnoszą nie chcemy przechowywać pustych koszyków.
Zakładamy, że brak wpisu koszyka o danym cart_id jest tożsame z istnieniem pustego koszyka.


## Mergowanie Koszyków

Scenariusz:

 - PWA ma sesje anonimowa i ma cart_id, czyli koszyk z produktami
 - PWA loguje sie na konto z istniejacym koszykiem
 - PWA ma oba cart_id w jednej sesji

 - PWA daje do wyboru userowi akcje:
    - nadpisz koszyk produktami z koszyka anonimowego
    - dopisane koszyk produktami z koszyka anonimowego
    - nie zmieniaj koszyka

 - W zależności od wybranej operacji PWA robi request:
    - "MERGE Cart z parametrem override": 
      produkty koszyka zalogowanego zostaja nadpisane produktami anonimowego, na koniec koszyk anonimowy ma byc pusty, do usuniecia

    - "MERGE Cart z parametrem add": 
      produkty koszyka zalogowanego zostaja dopisane produktami anonimowego, na koniec koszyk anonimowy ma byc pusty, do usuniecia


# Założenia domenowe:

Poprawny koszyk z pełnym zestawem danych oraz wyceną stanowi oferte, złożenie zamówienia jest zaakceptowaniem tej oferty przez klienta.  
Koszyk można bezpiecznie usunąć wtedy i tylko wtedy jeżeli nie ma przypisanego zamówienia lub zamówienie, do którego jest przypisany jest usuwane.  
Produkty, zniżki, dostawy(Items, Discounts, ShippingItems) można usunąć wtedy i tylko wtedy, jeżeli nie mają przypisanego ani zamówienia ani koszyka lub zamówienie/koszyk, do którego należą jest usuwane.

Co to znaczy, że koszyk jest poprawny:  
1. Koszyk zawiera poprawny adres rozliczeniowy.  
2. Koszyk zawiera w sobie informację o wybranej metodzie płatności i metoda ta jest dostępna dla podanego adresu rozliczeniowego.  
3. Koszyk zawiera poprawny adres dostawy.  
4. Koszyk zawiera w sobie informację o wybranej metodzie dostawy i metoda ta jest dostępna dla podanego adresu dostawy oraz dla wybranego zestawu produktów. 
5. Wszystkie produkty zawarte w koszyku są dostępne i da się je wycenić.  
6. Wszystkie kody zniżkowe/promocje zawarte w koszyku są poprawne i da się je naliczyć/wycenić.   

Procesowanie koszyka:
1. Klient wysyła dane w formacie JSON z opisem koszyka.
2. Dla klienta zostaje utworzony koszyk, lub istniejący już koszyk ulega modyfikacji. 
3. Koszyk podlega wycenie i weryfikacji.  
4. Klient dostaje informacje zwrotną, w tym, wycenę oraz informacje o brakach czy też niepoprawnościach w podanych danych. Zwrotka zawiera też dostępne dla potencjalnego zamówienia metody dostawy i metody płatności.  

Składanie zamówienia:  
1. Klient wysyła dane w formacie JSON zawierające opis koszyka wraz z wyceną otrzymaną wcześniej z serwera.  
2. Koszyk podelga ponownej wycenie i weryfikacji.  
3. Jeżeli koszyk jest poprawny, zawiera pełny zestaw danych oraz wycena nie uległa zmianie, następuje rezerwacja produktów i utworzenie zamówienia.    
4. W przeciwnym wypadku klient dostaje informacje o błędach, brakujących danych oraz zmianach w wycenie, zmówienie nie zostaje utworzone.

Czy jak usuwamy z systemu sklep to zostawiamy jego koszyki i zamówienia?


