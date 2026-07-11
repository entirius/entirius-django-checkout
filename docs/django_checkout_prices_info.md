
django_checkout_prices_info
===========================

do mangeto wpadają ceny:
- dla poszczegółnych przedmiotów:
- - price - jest to cena netto produktu (równa unit_price -  unit_tax_amount)
- - original_price - jest to cena produktu bez podatku podstawowa (nie specjalna) (równa base_unit_price / (1 + tax_rate))
- - tax_amount - jest to kwota taxu dla całej linii (równa total_tax_amount)
- - tax_percent - procentowa wartość taxu
- - price_incl_tax - kwota całej lini z podatkiem 
- - discount_amount - kwota naliczonego discountu dla całej linii
- - discount_percent - procentowa wartość discountu
- - qty_ordered - ilość produktu w danej linii

- dla całego zamówienia:
- - subtotal - sumowane  wartości pola row_total przedmiotów (pole row_total = price * qty_ordered)
- - tax_amount - sumowane  wartości pola tax_amount przedmiotów
- - subtotal_incl_tax - sumowane  wartości pola row_total_incl_tax przedmiotów (pole row_total_incl_tax = równego price_incl_tax * qty_ordered)
- - grand_total - suma row_total_incl_tax wszytskich produktów plus ceny dostawy z podatkiem (równa sum([row_total_incl_tax]) + shipping_total.shipping_incl_tax)
- - total_due - niezależnie co wyślemy ustala się na wartość grand total (W toku realizacji zamówienia kwota ta pomniejsza się o wartości invoiców/refundóœ)
- - discount_amount - sumowane  wartości pola discount_amount przedmiotów

Wyliczanie discountów:
- na dane sku
Aby wprowadzić discount na dany sku należy wpisać w Discount Rule: Code w pole sku rodzica tego sku
- na daną kategorię
Należy wybrać kategorie z rozwijanego paska
- na cały koszyk
Nic nie doprecyzowywać w sekcji Link to product by

Discount kwotowy nalicza się per item prooprocjanlnie według ceny linii (linia to produkt * qty)
