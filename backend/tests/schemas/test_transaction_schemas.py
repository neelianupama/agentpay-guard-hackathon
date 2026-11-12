"""Transaction validation, including UTC timestamp handling."""

from datetime import UTC, datetime
from decimal import Decimal

from app.schemas import Transaction, TransactionStatus
from tests.helpers import assert_invalid


def test_offset_timestamp_normalized_to_utc(tx):
    created_at = Transaction(**tx).created_at
    assert created_at.tzinfo is UTC
    assert created_at.isoformat() == "2026-11-12T06:30:00+00:00"


def test_iso_z_string_parsed_as_utc(tx):
    created_at = Transaction(**{**tx, "created_at": "2026-11-12T06:30:00Z"}).created_at
    assert created_at.tzinfo == UTC
    assert created_at == datetime(2026, 11, 12, 6, 30, tzinfo=UTC)


def test_risk_level_is_optional(tx):
    del tx["risk_level"]
    assert Transaction(**tx).risk_level is None


def test_naive_datetime_rejected(tx):
    assert_invalid(
        lambda: Transaction(**{**tx, "created_at": datetime(2026, 11, 12)}),
        "created_at",
        "timezone_aware",
    )


def test_unknown_status_rejected(tx):
    assert_invalid(lambda: Transaction(**{**tx, "status": "DONE"}), "status", "enum")


def test_status_is_enum(tx):
    assert Transaction(**tx).status is TransactionStatus.PENDING_REVIEW


def test_transaction_json_serialization(tx):
    assert Transaction(**tx).model_dump_json() == (
        '{"transaction_id":"txn_1","merchant_name":"Acme","amount":"10.00","currency":"EUR",'
        '"status":"PENDING_REVIEW","risk_level":"MEDIUM","created_at":"2026-11-12T06:30:00Z"}'
    )


def test_assignment_is_validated(tx):
    transaction = Transaction(**tx)
    assert_invalid(lambda: setattr(transaction, "amount", Decimal("-1")), "amount", "greater_than")
