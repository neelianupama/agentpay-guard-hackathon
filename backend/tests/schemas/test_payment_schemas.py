"""PaymentIntentRequest, PaymentIntent and TransactionDetails validation."""

from decimal import Decimal

import pytest

from app.schemas import PaymentIntent, PaymentIntentRequest, TransactionDetails
from tests.helpers import assert_invalid


def test_payment_intent_request_valid():
    req = PaymentIntentRequest(request_text="Pay Acme $5")
    assert req.request_text == "Pay Acme $5"


def test_payment_intent_request_rejects_blank_text():
    assert_invalid(lambda: PaymentIntentRequest(request_text="   "), "request_text", "string_too_short")


def test_payment_intent_valid(details):
    intent = PaymentIntent(request_text="Pay Acme Cloud $49.99", **details)
    assert intent.merchant_name == "Acme Cloud"
    assert intent.amount == Decimal("49.99")
    assert intent.currency == "USD"
    assert intent.request_text == "Pay Acme Cloud $49.99"


def test_amount_is_decimal(details):
    intent = PaymentIntent(request_text="x", **details)
    assert type(intent.amount) is Decimal


def test_json_amount_keeps_precision():
    parsed = TransactionDetails.model_validate_json(
        '{"merchant_name":"A","amount":0.1,"currency":"USD","description":"d","purpose":"p"}'
    )
    assert parsed.amount == Decimal("0.1")
    assert type(parsed.amount) is Decimal


def test_amount_serializes_as_string(details):
    dumped = TransactionDetails(**details).model_dump_json()
    assert '"amount":"49.99"' in dumped


@pytest.mark.parametrize(
    ("amount", "error_type"),
    [
        pytest.param("0", "greater_than", id="zero"),
        pytest.param("-5", "greater_than", id="negative"),
        pytest.param("1.005", "decimal_max_places", id="three-decimal-places"),
        pytest.param("NaN", "finite_number", id="nan"),
        pytest.param("Infinity", "finite_number", id="infinity"),
    ],
)
def test_invalid_amount_rejected(details, amount, error_type):
    assert_invalid(lambda: TransactionDetails(**{**details, "amount": amount}), "amount", error_type)


@pytest.mark.parametrize(
    "currency",
    [
        pytest.param("usd", id="lowercase"),
        pytest.param("US", id="two-letters"),
        pytest.param("USDT", id="four-letters"),
    ],
)
def test_invalid_currency_rejected(details, currency):
    assert_invalid(
        lambda: TransactionDetails(**{**details, "currency": currency}),
        "currency",
        "string_pattern_mismatch",
    )


def test_unknown_field_rejected(details):
    assert_invalid(lambda: TransactionDetails(**details, foo=1), "foo", "extra_forbidden")
