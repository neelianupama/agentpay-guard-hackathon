"""Fixtures for service-layer tests."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest

from app.schemas import PaymentIntent, TransactionDetails
from app.services.risk import RiskEngine

CLEAN = {
    "merchant_name": "Acme Cloud",
    "amount": Decimal("49.99"),
    "currency": "USD",
    "description": "Monthly hosting invoice",
    "purpose": "Infrastructure hosting",
}


@pytest.fixture
def make() -> Callable[..., TransactionDetails]:
    """Build TransactionDetails from a baseline that triggers no rules, with overrides."""

    def _make(**overrides: Any) -> TransactionDetails:
        return TransactionDetails(**{**CLEAN, **overrides})

    return _make


@pytest.fixture
def make_intent() -> Callable[..., PaymentIntent]:
    def _make(request_text: str = "Pay Acme Cloud for hosting", **overrides: Any) -> PaymentIntent:
        return PaymentIntent(**{**CLEAN, **overrides}, request_text=request_text)

    return _make


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine()
