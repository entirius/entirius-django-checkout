# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for v2 Pydantic schemas — field translation, formatting, null handling."""

from decimal import Decimal

from django_checkout.schemas.common import format_money, format_tax_rate


class TestFormatMoney:
    def test_integer(self):
        assert format_money(100) == "100.00"

    def test_decimal(self):
        assert format_money(Decimal("299.99")) == "299.99"

    def test_zero(self):
        assert format_money(Decimal("0")) == "0.00"

    def test_none(self):
        assert format_money(None) is None

    def test_float(self):
        assert format_money(19.99) == "19.99"

    def test_rounds_down(self):
        assert format_money(Decimal("10.999")) == "11.00"

    def test_large_value(self):
        assert format_money(Decimal("999999.99")) == "999999.99"

    def test_small_value(self):
        assert format_money(Decimal("0.01")) == "0.01"


class TestFormatTaxRate:
    def test_integer_23(self):
        assert format_tax_rate(23) == "0.23"

    def test_integer_8(self):
        assert format_tax_rate(8) == "0.08"

    def test_decimal_023(self):
        assert format_tax_rate(Decimal("0.23")) == "0.23"

    def test_zero(self):
        assert format_tax_rate(0) == "0.00"

    def test_none(self):
        assert format_tax_rate(None) is None

    def test_decimal_string_passthrough(self):
        """Already decimal format stays as-is."""
        assert format_tax_rate(Decimal("0.08")) == "0.08"

    def test_integer_100(self):
        """100% tax rate edge case."""
        assert format_tax_rate(100) == "1.00"


class TestItemResponse:
    def test_from_domain_basic(self):
        """Test basic field rename from ValidatedItemData to ItemResponse."""
        from django_checkout.schemas.responses.cart import ItemResponse

        class MockItem:
            sku = "TEST-001"
            quantity = Decimal("2")
            status = type("S", (), {"value": "valid"})()
            is_gratis = False
            base_unit_price = Decimal("100.00")
            base_total_price = Decimal("200.00")
            special_unit_price = None
            special_total_price = None
            special_percent = None
            unit_price = Decimal("90.00")
            unit_price_netto = Decimal("73.17")
            total_price = Decimal("180.00")
            total_price_netto = Decimal("146.34")
            tax_rate = Decimal("23")
            unit_tax_amount = Decimal("16.83")
            total_tax_amount = Decimal("33.66")
            discount_amount = Decimal("20.00")
            discount_amount_netto = Decimal("16.26")

        result = ItemResponse.from_domain(MockItem())

        assert result.sku == "TEST-001"
        assert result.quantity == 2
        assert result.unit_gross == "100.00"
        assert result.final_unit_gross == "90.00"
        assert result.final_unit_net == "73.17"
        assert result.tax_rate == "0.23"
        assert result.discount_gross == "20.00"
        assert result.discount_net == "16.26"
        assert result.has_special_price is False

    def test_from_domain_with_special_price(self):
        from django_checkout.schemas.responses.cart import ItemResponse

        class MockItem:
            sku = "PROMO-001"
            quantity = Decimal("1")
            status = type("S", (), {"value": "valid"})()
            is_gratis = False
            base_unit_price = Decimal("200.00")
            base_total_price = Decimal("200.00")
            special_unit_price = Decimal("150.00")
            special_total_price = Decimal("150.00")
            special_percent = 25
            unit_price = Decimal("150.00")
            unit_price_netto = Decimal("121.95")
            total_price = Decimal("150.00")
            total_price_netto = Decimal("121.95")
            tax_rate = Decimal("23")
            unit_tax_amount = Decimal("28.05")
            total_tax_amount = Decimal("28.05")
            discount_amount = Decimal("0")
            discount_amount_netto = Decimal("0")

        result = ItemResponse.from_domain(MockItem())

        assert result.has_special_price is True
        assert result.special_unit_gross == "150.00"
        assert result.percent_off == 25

    def test_from_domain_gratis_item(self):
        from django_checkout.schemas.responses.cart import ItemResponse

        class MockItem:
            sku = "FREE-001"
            quantity = Decimal("1")
            status = type("S", (), {"value": "valid"})()
            is_gratis = True
            base_unit_price = Decimal("50.00")
            base_total_price = Decimal("50.00")
            special_unit_price = None
            special_total_price = None
            special_percent = None
            unit_price = Decimal("0.01")
            unit_price_netto = Decimal("0.01")
            total_price = Decimal("0.01")
            total_price_netto = Decimal("0.01")
            tax_rate = Decimal("23")
            unit_tax_amount = Decimal("0.00")
            total_tax_amount = Decimal("0.00")
            discount_amount = Decimal("49.99")
            discount_amount_netto = Decimal("40.64")

        result = ItemResponse.from_domain(MockItem())

        assert result.is_gratis is True
        assert result.discount_gross == "49.99"


class TestAddressResponse:
    def test_from_domain_billing(self):
        from django_checkout.schemas.responses.address import BillingAddressResponse

        class MockAddr:
            firstname = "Jan"
            lastname = "Kowalski"
            street = "Test 1"
            city = "Warszawa"
            postcode = "00-001"
            country_code = "PL"
            telephone = "500500500"
            dialling_code = "+48"
            company = None
            is_company = False
            tax_id = None
            address_id = 42
            external_id = None
            source = "address_book"
            email = "jan@test.com"
            requested_invoice = False
            vat_validation_status = None

        result = BillingAddressResponse.from_domain(MockAddr())

        assert result.firstname == "Jan"
        assert result.address_id == 42
        assert result.email == "jan@test.com"
        assert result.is_company is False

    def test_from_domain_none(self):
        from django_checkout.schemas.responses.address import CheckoutAddressResponse

        assert CheckoutAddressResponse.from_domain(None) is None


class TestErrorMapper:
    def test_string_message(self):
        from django_checkout.api.v2.exceptions import map_to_v2_details

        details = map_to_v2_details(["Something went wrong"])
        assert len(details) == 1
        assert details[0]["code"] == "GENERAL_WARNING"
        assert details[0]["scope"] == "cart"

    def test_error_info_object(self):
        from django_checkout.api.v2.exceptions import map_to_v2_details

        class MockError:
            code = "ITEM_OUT_OF_STOCK"
            affected_field = "items[0].quantity"
            message = "Not enough stock"

        details = map_to_v2_details([MockError()])
        assert len(details) == 1
        assert details[0]["code"] == "ITEM_OUT_OF_STOCK"
        assert details[0]["scope"] == "items"

    def test_mixed_messages(self):
        from django_checkout.api.v2.exceptions import map_to_v2_details

        class MockError:
            code = "DISCOUNT_EXPIRED"
            affected_field = "codes[0].code"
            message = "Code expired"

        details = map_to_v2_details(["Warning text", MockError()])
        assert len(details) == 2
        assert details[0]["code"] == "GENERAL_WARNING"
        assert details[1]["code"] == "DISCOUNT_EXPIRED"
        assert details[1]["scope"] == "discounts"
