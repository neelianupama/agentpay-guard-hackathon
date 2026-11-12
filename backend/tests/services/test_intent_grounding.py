"""Grounding checks: extracted values must be traceable to the request text."""

from decimal import Decimal

import pytest

from app.services.intent.grounding import (
    amount_is_grounded,
    amounts_in_text,
    currency_is_grounded,
    merchant_is_grounded,
)


class TestMerchant:
    @pytest.mark.parametrize(
        "name", ["ABC Technologies", "abc technologies", "ABC   Technologies"]
    )
    def test_present_merchant_is_grounded(self, name):
        assert merchant_is_grounded(name, "Pay 250 USD to ABC Technologies for software.")

    @pytest.mark.parametrize("name", ["XYZ Corp", "ABC Technologies Inc", "   "])
    def test_absent_merchant_is_not_grounded(self, name):
        assert not merchant_is_grounded(name, "Pay 250 USD to ABC Technologies for software.")


class TestAmount:
    def test_numbers_extracted_ignoring_thousands_separators(self):
        values = amounts_in_text("Pay 1,250.50 and 1,00,000 and $30 and USD250")
        assert {Decimal("1250.50"), Decimal("100000"), Decimal("30"), Decimal("250")} <= values

    @pytest.mark.parametrize(
        ("amount", "text"),
        [
            ("250", "Pay 250 USD"),
            ("250.00", "Pay 250 USD"),
            ("250", "Pay $250.00"),
            ("1250.50", "Pay 1,250.50 EUR"),
            ("99.99", "Pay €99.99 now."),
        ],
    )
    def test_amount_written_in_text_is_grounded(self, amount, text):
        assert amount_is_grounded(Decimal(amount), text)

    @pytest.mark.parametrize(
        ("amount", "text"),
        [
            ("250", "Pay two hundred fifty dollars"),
            ("500", "Pay 250 USD twice"),
            ("25", "Pay 250 USD"),
            ("1000", "Pay ABC for services"),
        ],
    )
    def test_amount_not_written_in_text_is_not_grounded(self, amount, text):
        assert not amount_is_grounded(Decimal(amount), text)


class TestCurrency:
    @pytest.mark.parametrize(
        ("code", "text"),
        [
            ("USD", "Pay 250 USD"),
            ("USD", "pay 250 usd"),
            ("USD", "Pay 250 US dollars"),
            ("USD", "Pay US$250"),
            ("EUR", "Pay €40"),
            ("EUR", "Pay 40 euros"),
            ("GBP", "Pay £40"),
            ("GBP", "Pay 40 pounds sterling"),
            ("CAD", "Pay C$40"),
            ("CAD", "Pay 40 Canadian dollars"),
            ("AUD", "Pay A$40"),
            ("INR", "Pay ₹5000"),
            ("INR", "Pay 5000 INR"),
            ("INR", "Pay 5000 Indian rupees"),
            ("TRY", "Pay 40 TRY"),
        ],
    )
    def test_stated_currency_is_grounded(self, code, text):
        assert currency_is_grounded(code, text)

    @pytest.mark.parametrize(
        ("code", "text"),
        [
            ("USD", "Pay $250"),  # "$" alone is ambiguous
            ("USD", "Pay 250 dollars"),
            ("USD", "Pay 250 to ABC"),  # no default currency
            ("AUD", "Pay CA$40"),  # "a$" inside "ca$" must not count
            ("EUR", "Pay 40 to Europe Travel"),
            ("INR", "Pay 5000 rupees"),  # ambiguous rupee
            ("TRY", "Please try to pay 40"),  # lowercase word, not a code
            ("CAD", "Pay the decade fee of 40"),
            ("EUR", "Pay 40 USD"),  # no conversion
        ],
    )
    def test_unstated_or_ambiguous_currency_is_not_grounded(self, code, text):
        assert not currency_is_grounded(code, text)
