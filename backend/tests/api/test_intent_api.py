"""POST /api/v1/intent/parse (Groq SDK mocked; no network, no API credits)."""

from dataclasses import replace

import groq
import pytest

from app.api.routes import intent as intent_route
from app.api.routes.intent import get_intent_extractor
from app.api.routes.risk import get_risk_engine
from app.core.config import get_settings
from app.main import app
from app.schemas import ParsedPaymentIntent
from app.services.risk import RiskEngine
from tests.intent_fakes import (
    EXAMPLE_TEXT,
    FAKE_SECRET,
    FakeGroq,
    make_extractor,
    model_output,
    provider_request,
    status_error,
)

URL = "/api/v1/intent/parse"


@pytest.fixture
def fake():
    """Install a fake Groq client for the endpoint; tests may reconfigure it."""
    fake = FakeGroq()
    app.dependency_overrides[get_intent_extractor] = lambda: make_extractor(fake)
    yield fake
    app.dependency_overrides.pop(get_intent_extractor, None)


def parse(client, text=EXAMPLE_TEXT):
    return client.post(URL, json={"request_text": text})


class TestSuccess:
    def test_example_request(self, client, fake):
        response = parse(client)
        assert response.status_code == 200
        assert response.json() == {
            "merchant_name": "ABC Technologies",
            "amount": "250",
            "currency": "USD",
            "description": "software services",
            "purpose": None,
            "missing_fields": ["purpose"],
            "clarification_needed": True,
        }

    def test_response_matches_contract(self, client, fake):
        ParsedPaymentIntent.model_validate(parse(client).json())

    def test_complete_request_needs_no_clarification(self, client, fake):
        fake.content = model_output(purpose="Q3 tooling budget", clarification_needed=False)
        body = parse(client, EXAMPLE_TEXT + " It is for the Q3 tooling budget.").json()
        assert body["missing_fields"] == []
        assert body["clarification_needed"] is False

    def test_incomplete_request(self, client, fake):
        fake.content = model_output(amount=None, currency=None)
        body = parse(client, "Pay ABC Technologies for software services.").json()
        assert body["amount"] is None and body["currency"] is None
        assert body["missing_fields"] == ["amount", "currency", "purpose"]
        assert body["clarification_needed"] is True

    def test_inr_returned_as_stated_without_conversion(self, client, fake):
        fake.content = model_output(amount="5000", currency="INR")
        body = parse(client, "Pay ₹5000 to ABC Technologies for software services.").json()
        assert (body["amount"], body["currency"]) == ("5000", "INR")

    def test_request_text_whitespace_stripped_before_model(self, client, fake):
        parse(client, f"   {EXAMPLE_TEXT}   ")
        assert fake.last_messages[1]["content"] == (
            f"<payment_request>\n{EXAMPLE_TEXT}\n</payment_request>"
        )


class TestNoRiskOrPayment:
    def test_response_has_no_risk_decision_or_payment_fields(self, client, fake):
        body = parse(client).json()
        forbidden = {"risk_score", "risk_level", "decision", "approved", "transaction_id", "status"}
        assert forbidden.isdisjoint(body)

    def test_risk_engine_not_invoked(self, client, fake, monkeypatch):
        def fail(*args, **kwargs):
            raise AssertionError("intent parsing must not invoke the risk engine")

        for method in ("assess", "evaluate_rules", "score", "decide"):
            monkeypatch.setattr(RiskEngine, method, fail)
        app.dependency_overrides[get_risk_engine] = fail
        try:
            assert parse(client).status_code == 200
        finally:
            app.dependency_overrides.pop(get_risk_engine, None)

    def test_exactly_one_model_call_and_nothing_else(self, client, fake):
        parse(client)
        assert len(fake.calls) == 1
        assert "tools" not in fake.calls[0] and "functions" not in fake.calls[0]

    def test_no_payment_routes_exist(self, client):
        # Updated in Step 4: the payments workflow now exists, but intent parsing still
        # exposes only /intent/parse, and the full route set is pinned explicitly.
        paths = set(client.get("/openapi.json").json()["paths"])
        assert {p for p in paths if p.startswith("/api/v1/intent")} == {"/api/v1/intent/parse"}
        assert paths == {
            "/health",
            "/api/v1/risk/assess",
            "/api/v1/intent/parse",
            "/api/v1/payments",
            "/api/v1/payments/{transaction_id}",
            "/api/v1/payments/{transaction_id}/order",
            "/api/v1/payments/{transaction_id}/authorize",
            "/api/v1/payments/{transaction_id}/approve",
            "/api/v1/payments/{transaction_id}/reject",
            "/api/v1/payments/{transaction_id}/capture",
            "/api/v1/payments/{transaction_id}/void",
            "/api/v1/audit/events",  # Step 7B
        }


class TestInputValidation:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"request_text": ""},
            {"request_text": "   "},
            {"request_text": "x" * 1001},
            {"request_text": 123},
            {"request_text": EXAMPLE_TEXT, "decision": "ALLOW"},
            {"request_text": EXAMPLE_TEXT, "risk_score": 0},
            {"request_text": EXAMPLE_TEXT, "model": "other-model"},
        ],
    )
    def test_invalid_request_rejected_without_model_call(self, client, fake, payload):
        response = client.post(URL, json=payload)
        assert response.status_code == 422
        assert fake.calls == []


class TestErrors:
    @pytest.mark.parametrize(
        ("error", "status", "code"),
        [
            (groq.APITimeoutError(request=provider_request()), 504, "INTENT_TIMEOUT"),
            (
                groq.APIConnectionError(request=provider_request()),
                503,
                "INTENT_SERVICE_UNAVAILABLE",
            ),
            (status_error(groq.RateLimitError, 429), 429, "INTENT_RATE_LIMITED"),
            (status_error(groq.NotFoundError, 404), 503, "INTENT_MODEL_UNAVAILABLE"),
            (status_error(groq.AuthenticationError, 401), 503, "INTENT_SERVICE_UNAVAILABLE"),
            (status_error(groq.InternalServerError, 500), 503, "INTENT_SERVICE_UNAVAILABLE"),
            (status_error(groq.BadRequestError, 400), 502, "INTENT_EXTRACTION_FAILED"),
        ],
    )
    def test_provider_errors_are_safe(self, client, fake, error, status, code):
        fake.error = error
        response = parse(client)
        assert response.status_code == status
        body = response.json()
        assert body["code"] == code
        assert set(body) == {"code", "message"}
        assert FAKE_SECRET not in response.text
        assert "groq" not in response.text.lower()

    @pytest.mark.parametrize(
        "content", ["not json", model_output(decision="ALLOW"), model_output(amount=250)]
    )
    def test_malformed_model_output(self, client, fake, content):
        fake.content = content
        response = parse(client)
        assert response.status_code == 502
        assert response.json()["code"] == "INTENT_EXTRACTION_FAILED"
        assert "ALLOW" not in response.text

    def test_truncated_model_output(self, client, fake):
        fake.finish_reason = "length"
        assert parse(client).status_code == 502


class TestConfiguration:
    def test_missing_api_key_returns_503(self, client):
        settings = replace(get_settings(), groq_api_key=None)
        app.dependency_overrides[get_settings] = lambda: settings
        try:
            response = parse(client)
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert response.status_code == 503
        assert response.json()["code"] == "INTENT_SERVICE_UNAVAILABLE"

    def test_extractor_built_from_settings(self, monkeypatch):
        captured = {}

        class SpyGroq:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(intent_route.groq, "Groq", SpyGroq)
        intent_route._build_extractor.cache_clear()
        settings = replace(
            get_settings(), groq_api_key=FAKE_SECRET, groq_model="some/model"
        )
        try:
            extractor = get_intent_extractor(settings)
        finally:
            intent_route._build_extractor.cache_clear()
        assert extractor.model == "some/model"
        assert captured["api_key"] == FAKE_SECRET
        assert captured["timeout"] == settings.groq_timeout_seconds
        assert captured["max_retries"] == intent_route.GROQ_MAX_RETRIES

    def test_settings_repr_hides_secrets(self):
        settings = replace(
            get_settings(),
            groq_api_key=FAKE_SECRET,
            paypal_client_secret=FAKE_SECRET,
            elastic_api_key=FAKE_SECRET,
        )
        assert FAKE_SECRET not in repr(settings)

    def test_default_model(self):
        from app.core.config import DEFAULT_GROQ_MODEL

        assert DEFAULT_GROQ_MODEL == "openai/gpt-oss-120b"
        assert 0 < get_settings().groq_timeout_seconds <= 60


class TestOpenAPI:
    def test_documented(self, client):
        spec = client.get("/openapi.json").json()
        op = spec["paths"]["/api/v1/intent/parse"]["post"]
        assert op["operationId"] == "parse_payment_intent"
        assert {"200", "422", "429", "502", "503", "504"} <= set(op["responses"])
        assert "ParsedPaymentIntent" in spec["components"]["schemas"]
