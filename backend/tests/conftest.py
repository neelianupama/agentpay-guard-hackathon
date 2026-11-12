"""Shared pytest fixtures.

Fixtures return fresh dicts each time, so tests can modify them freely.
All values are minimal structural inputs for validation, not sample payment data.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import groq.resources.chat.completions as groq_completions
import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes.audit import get_audit_recorder
from app.main import app
from app.services.audit import AuditRecorder


@pytest.fixture(autouse=True)
def _block_live_groq_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would reach the real Groq API (and spend credits)."""

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Tests must not call the real Groq API; use tests.intent_fakes")

    monkeypatch.setattr(groq_completions.Completions, "create", _blocked)


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that would open a real HTTP connection (e.g. to PayPal).

    Tests must inject ``httpx.MockTransport``; FastAPI's TestClient uses its
    own in-process transport and is unaffected.
    """

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Tests must not make real network requests; use httpx.MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked)


@pytest.fixture(autouse=True)
def _isolated_audit_recorder():
    """Each test gets its own fallback-only recorder (never a real Elastic connection).

    Tests that exercise Elastic install their own recorder via dependency override.
    """
    recorder = AuditRecorder(None)
    app.dependency_overrides[get_audit_recorder] = lambda: recorder
    yield recorder
    if app.dependency_overrides.get(get_audit_recorder) is not None:
        app.dependency_overrides.pop(get_audit_recorder, None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def details() -> dict[str, Any]:
    """Valid TransactionDetails input."""
    return {
        "merchant_name": "Acme Cloud",
        "amount": "49.99",
        "currency": "USD",
        "description": "Monthly hosting invoice",
        "purpose": "Infrastructure hosting",
    }


@pytest.fixture
def risk(details: dict[str, Any]) -> dict[str, Any]:
    """Valid RiskAssessment input."""
    return {
        "transaction": details,
        "risk_score": 72,
        "risk_level": "HIGH",
        "decision": "REVIEW",
        "reasons": ["Amount exceeds agent limit"],
    }


@pytest.fixture
def tx() -> dict[str, Any]:
    """Valid Transaction input, created_at in UTC+05:30."""
    return {
        "transaction_id": "txn_1",
        "merchant_name": "Acme",
        "amount": Decimal("10.00"),
        "currency": "EUR",
        "status": "PENDING_REVIEW",
        "risk_level": "MEDIUM",
        "created_at": datetime(
            2026, 11, 12, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))
        ),
    }

