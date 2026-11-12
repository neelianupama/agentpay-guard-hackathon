"""ParsedPaymentIntent contract."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import ParsedPaymentIntent, PaymentDetailField
from tests.helpers import assert_invalid

COMPLETE = {
    "merchant_name": "ABC Technologies",
    "amount": "250",
    "currency": "USD",
    "description": "Software services",
    "purpose": "Engineering tooling",
    "missing_fields": [],
    "clarification_needed": False,
}


def build(**overrides):
    return ParsedPaymentIntent(**{**COMPLETE, **overrides})


def test_complete_intent_is_valid():
    intent = build()
    assert intent.amount == Decimal("250")
    assert intent.missing_fields == []
    assert intent.clarification_needed is False


def test_amount_serialized_as_string():
    assert build().model_dump(mode="json")["amount"] == "250"


def test_all_details_may_be_null_when_listed_missing():
    intent = ParsedPaymentIntent(
        missing_fields=list(PaymentDetailField), clarification_needed=True
    )
    assert intent.merchant_name is None and intent.amount is None


def test_missing_fields_use_canonical_names():
    assert [f.value for f in PaymentDetailField] == [
        "merchant_name",
        "amount",
        "currency",
        "description",
        "purpose",
    ]


def test_missing_fields_must_match_null_details():
    with pytest.raises(ValidationError, match="missing_fields"):
        build(purpose=None)  # null but not listed
    with pytest.raises(ValidationError, match="missing_fields"):
        build(missing_fields=["purpose"], clarification_needed=True)  # listed but present


def test_missing_fields_must_be_in_canonical_order():
    with pytest.raises(ValidationError, match="canonical order"):
        build(
            amount=None,
            purpose=None,
            missing_fields=["purpose", "amount"],
            clarification_needed=True,
        )


def test_clarification_required_when_details_missing():
    with pytest.raises(ValidationError, match="clarification_needed"):
        build(purpose=None, missing_fields=["purpose"], clarification_needed=False)


def test_clarification_may_be_needed_even_when_complete():
    assert build(clarification_needed=True).clarification_needed is True


@pytest.mark.parametrize(
    ("field", "value", "error_type"),
    [
        ("amount", "0", "greater_than"),
        ("amount", "-5", "greater_than"),
        ("amount", "1.999", "decimal_max_places"),
        ("currency", "usd", "string_pattern_mismatch"),
        ("currency", "US", "string_pattern_mismatch"),
        ("merchant_name", "x" * 201, "string_too_long"),
        ("purpose", "x" * 201, "string_too_long"),
        ("description", "x" * 1001, "string_too_long"),
    ],
)
def test_details_reuse_transaction_constraints(field, value, error_type):
    assert_invalid(lambda: build(**{field: value}), field, error_type)


@pytest.mark.parametrize("field", ["risk_score", "risk_level", "decision", "approved"])
def test_no_risk_or_approval_fields_accepted(field):
    assert_invalid(lambda: build(**{field: "ALLOW"}), field, "extra_forbidden")


def test_unknown_missing_field_rejected():
    with pytest.raises(ValidationError):
        build(missing_fields=["risk_score"], clarification_needed=True)
