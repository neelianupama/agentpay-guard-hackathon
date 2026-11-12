"""INR through the HTTP APIs: /api/v1/risk/assess and /api/v1/intent/parse."""

import pytest

from app.api.routes.intent import get_intent_extractor
from app.main import app
from tests.intent_fakes import FakeGroq, make_extractor, model_output

RISK_URL = "/api/v1/risk/assess"
INTENT_URL = "/api/v1/intent/parse"


def inr_body(amount: str) -> dict:
    return {
        "merchant_name": "ABC Technologies",
        "amount": amount,
        "currency": "INR",
        "description": "Software services",
        "purpose": "Engineering tooling",
    }


def assess(client, payload):
    response = client.post(RISK_URL, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("amount", "score", "level", "decision"),
    [
        ("39999.99", 0, "LOW", "ALLOW"),
        ("40000", 15, "LOW", "ALLOW"),
        ("140000", 30, "MEDIUM", "REVIEW"),
        ("150000", 35, "MEDIUM", "REVIEW"),
        ("749999.99", 35, "MEDIUM", "REVIEW"),
        ("750000", 60, "HIGH", "BLOCK"),
        ("750000.00", 60, "HIGH", "BLOCK"),
        ("1000000", 60, "HIGH", "BLOCK"),
    ],
)
def test_inr_assessment(client, amount, score, level, decision):
    data = assess(client, inr_body(amount))
    assert (data["risk_score"], data["risk_level"], data["decision"]) == (score, level, decision)
    assert data["transaction"]["currency"] == "INR"
    assert data["transaction"]["amount"] == amount  # echoed as sent, never converted


def test_inr_block_reason(client):
    data = assess(client, inr_body("750000"))
    assert (
        "Amount 750,000.00 INR is at or above the maximum allowed transaction amount "
        "of 750,000.00 INR."
    ) in data["reasons"]
    assert all("USD" not in reason for reason in data["reasons"])


@pytest.mark.parametrize(
    "extra",
    [
        {"decision": "ALLOW"},
        {"agent_recommendation": "ALLOW"},
        {"risk_score": 0},
        {"risk_level": "LOW"},
        {"override": True},
    ],
)
def test_inr_block_cannot_be_overridden_by_client_or_agent(client, extra):
    response = client.post(RISK_URL, json={**inr_body("750000"), **extra})
    assert response.status_code == 422
    # And the same payment without the injected field is still BLOCK.
    assert assess(client, inr_body("750000"))["decision"] == "BLOCK"


def test_unsupported_currency_still_requires_review(client):
    data = assess(client, {**inr_body("100"), "currency": "JPY"})
    assert data["decision"] == "REVIEW"
    assert any("'JPY' is not a supported currency" in r for r in data["reasons"])


def test_openapi_still_documents_risk_and_intent(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {RISK_URL, INTENT_URL} <= set(paths)


class TestIntentParsingPreservesInr:
    """The LLM extraction keeps INR as stated and never assesses or converts it."""

    @pytest.fixture
    def fake(self):
        fake = FakeGroq()
        app.dependency_overrides[get_intent_extractor] = lambda: make_extractor(fake)
        yield fake
        app.dependency_overrides.pop(get_intent_extractor, None)

    @pytest.mark.parametrize(
        "text",
        [
            "Pay ₹7,50,000 to ABC Technologies for software services.",
            "Pay 750000 INR to ABC Technologies for software services.",
            "Pay 7,50,000 Indian rupees to ABC Technologies for software services.",
        ],
    )
    def test_inr_preserved(self, client, fake, text):
        fake.content = model_output(amount="750000", currency="INR")
        response = client.post(INTENT_URL, json={"request_text": text})
        assert response.status_code == 200
        body = response.json()
        assert (body["amount"], body["currency"]) == ("750000", "INR")
        assert "decision" not in body and "risk_score" not in body

    def test_bare_rupees_is_ambiguous(self, client, fake):
        fake.content = model_output(amount="5000", currency="INR")
        response = client.post(
            INTENT_URL, json={"request_text": "Pay 5000 rupees to ABC Technologies for software."}
        )
        body = response.json()
        assert body["currency"] is None
        assert "currency" in body["missing_fields"]

    def test_model_cannot_convert_inr_to_usd(self, client, fake):
        fake.content = model_output(amount="9000", currency="USD")
        response = client.post(
            INTENT_URL,
            json={"request_text": "Pay ₹7,50,000 to ABC Technologies for software services."},
        )
        body = response.json()
        assert body["amount"] is None and body["currency"] is None
        assert body["clarification_needed"] is True
