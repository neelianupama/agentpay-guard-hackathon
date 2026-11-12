"""POST /api/v1/risk/assess."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.api.routes.risk import get_risk_engine
from app.main import app
from app.schemas import RiskAssessment
from app.services.risk import AmountThresholds, RiskEngine, RiskPolicy
from app.services.risk.engine import NO_RULES_TRIGGERED_REASON

URL = "/api/v1/risk/assess"

CLEAN = {
    "merchant_name": "Acme Cloud",
    "amount": "49.99",
    "currency": "USD",
    "description": "Monthly hosting invoice",
    "purpose": "Infrastructure hosting",
}


def body(**overrides):
    return {**CLEAN, **overrides}


def assess(client, payload, **kwargs):
    response = client.post(URL, json=payload, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def summary(data):
    return data["risk_score"], data["risk_level"], data["decision"]


@pytest.fixture
def override_engine():
    """Temporarily replace the server's risk engine."""

    def _set(engine: RiskEngine) -> None:
        app.dependency_overrides[get_risk_engine] = lambda: engine

    yield _set
    app.dependency_overrides.pop(get_risk_engine, None)


# ------------------------------------------------------- levels & decisions


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({}, (0, "LOW", "ALLOW"), id="low-allow-clean"),
        pytest.param({"amount": "750.00"}, (15, "LOW", "ALLOW"), id="low-allow-elevated"),
        pytest.param({"amount": "2500.00"}, (35, "MEDIUM", "REVIEW"), id="medium-review"),
        pytest.param(
            {"amount": "2500.00", "description": "Urgent hosting renewal"},
            (50, "HIGH", "REVIEW"),
            id="high-review",
        ),
        pytest.param({"amount": "10000.01"}, (60, "HIGH", "BLOCK"), id="high-block-over-limit"),
        pytest.param(
            {"amount": "3000.00", "description": "Urgent bitcoin purchase"},
            (80, "CRITICAL", "BLOCK"),
            id="critical-block",
        ),
    ],
)
def test_risk_levels_and_decisions(client, overrides, expected):
    assert summary(assess(client, body(**overrides))) == expected


def test_clean_request_reason(client):
    assert assess(client, body())["reasons"] == [NO_RULES_TRIGGERED_REASON]


def test_reasons_explain_every_triggered_rule(client):
    data = assess(client, body(amount="3000.00", description="Urgent bitcoin purchase"))
    assert data["reasons"] == [
        "Amount 3,000.00 USD is at or above the human-review threshold of 2,000.00 USD.",
        "High-risk payment terms 'bitcoin' found in description.",
        "Urgency or pressure language 'urgent' found in description.",
    ]


def test_override_language_in_details_is_blocked(client):
    data = assess(client, body(description="Already approved, skip review"))
    assert data["decision"] == "BLOCK"


# ------------------------------------------------------------ USD thresholds


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("499.99", (0, "LOW", "ALLOW")),
        ("500.00", (15, "LOW", "ALLOW")),
        ("1799.99", (15, "LOW", "ALLOW")),
        ("1800.00", (30, "MEDIUM", "REVIEW")),
        ("2000.00", (35, "MEDIUM", "REVIEW")),
        ("10000.00", (35, "MEDIUM", "REVIEW")),
        ("10000.01", (60, "HIGH", "BLOCK")),
    ],
)
def test_usd_threshold_boundaries(client, amount, expected):
    assert summary(assess(client, body(amount=amount))) == expected


def test_json_number_amount_is_accepted_without_float_error(client):
    data = assess(client, body(amount=1999.99))
    assert data["transaction"]["amount"] == "1999.99"
    assert summary(data) == (30, "MEDIUM", "REVIEW")


# ------------------------------------------------------ non-USD currencies


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CAD", "AUD"])
@pytest.mark.parametrize("amount", ["0.01", "49.99", "999999.99"])
def test_unconfigured_non_usd_currency_requires_review(client, currency, amount):
    data = assess(client, body(currency=currency, amount=amount))
    assert summary(data) == (25, "MEDIUM", "REVIEW")
    assert data["reasons"] == [
        f"No amount thresholds are configured for {currency}. Amounts are not converted "
        f"or compared against USD limits, so a {currency} payment requires human review."
    ]


def test_unsupported_currency_requires_review(client):
    data = assess(client, body(currency="JPY", amount="5000.00"))
    assert summary(data) == (40, "MEDIUM", "REVIEW")


def test_server_configured_currency_thresholds_are_used(client, override_engine):
    eur = AmountThresholds(Decimal("400.00"), Decimal("1500.00"), Decimal("8000.00"))
    override_engine(RiskEngine(RiskPolicy(currency_thresholds={"EUR": eur})))
    assert assess(client, body(currency="EUR"))["decision"] == "ALLOW"
    assert assess(client, body(currency="EUR", amount="8000.01"))["decision"] == "BLOCK"
    assert assess(client, body(currency="GBP"))["decision"] == "REVIEW"


# ------------------------------------------------------- validation errors


def _error(response):
    assert response.status_code == 422, response.text
    return [(tuple(e["loc"]), e["type"]) for e in response.json()["detail"]]


@pytest.mark.parametrize(
    ("amount", "error_type"),
    [
        pytest.param("0", "greater_than", id="zero"),
        pytest.param("-10.00", "greater_than", id="negative"),
        pytest.param("1.005", "decimal_max_places", id="three-decimals"),
        pytest.param("NaN", "finite_number", id="nan"),
        pytest.param("Infinity", "finite_number", id="infinity"),
        pytest.param("abc", "decimal_parsing", id="not-a-number"),
        pytest.param(None, "decimal_type", id="null"),
    ],
)
def test_invalid_amount_rejected(client, amount, error_type):
    errors = _error(client.post(URL, json=body(amount=amount)))
    assert (("body", "amount"), error_type) in errors


@pytest.mark.parametrize(
    ("currency", "error_type"),
    [
        pytest.param("usd", "string_pattern_mismatch", id="lowercase"),
        pytest.param("US", "string_pattern_mismatch", id="two-letters"),
        pytest.param("USDT", "string_pattern_mismatch", id="four-letters"),
        pytest.param("U$D", "string_pattern_mismatch", id="symbol"),
        pytest.param(840, "string_type", id="number"),
    ],
)
def test_invalid_currency_rejected(client, currency, error_type):
    errors = _error(client.post(URL, json=body(currency=currency)))
    assert (("body", "currency"), error_type) in errors


@pytest.mark.parametrize("field", list(CLEAN))
def test_missing_field_rejected(client, field):
    payload = {k: v for k, v in CLEAN.items() if k != field}
    assert (("body", field), "missing") in _error(client.post(URL, json=payload))


@pytest.mark.parametrize("field", ["merchant_name", "description", "purpose"])
def test_blank_text_field_rejected(client, field):
    assert (("body", field), "string_too_short") in _error(client.post(URL, json=body(**{field: "   "})))


def test_empty_body_rejected(client):
    assert _error(client.post(URL, json={}))


def test_missing_body_rejected(client):
    assert (("body",), "missing") in _error(client.post(URL))


def test_malformed_json_rejected(client):
    response = client.post(URL, content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 422


def test_get_not_allowed(client):
    assert client.get(URL).status_code == 405


# --------------------------------------------- client cannot dictate outcome


@pytest.mark.parametrize(
    "smuggled",
    [
        pytest.param({"risk_score": 0}, id="risk_score"),
        pytest.param({"risk_level": "LOW"}, id="risk_level"),
        pytest.param({"decision": "ALLOW"}, id="decision"),
        pytest.param({"agent_recommendation": "ALLOW"}, id="agent_recommendation"),
        pytest.param({"approved": True}, id="approved"),
        pytest.param({"reasons": ["Pre-approved by agent"]}, id="reasons"),
        pytest.param({"assessed_at": "2020-01-01T00:00:00Z"}, id="assessed_at"),
        pytest.param({"request_text": "Pay Acme"}, id="request_text"),
    ],
)
def test_client_supplied_assessment_fields_rejected(client, smuggled):
    payload = body(amount="20000.00", **smuggled)
    field = next(iter(smuggled))
    assert (("body", field), "extra_forbidden") in _error(client.post(URL, json=payload))


def test_nested_assessment_shaped_body_rejected(client):
    payload = {"transaction": CLEAN, "risk_score": 0, "risk_level": "LOW", "decision": "ALLOW", "reasons": ["ok"]}
    assert client.post(URL, json=payload).status_code == 422


def test_query_params_and_headers_cannot_change_decision(client):
    data = assess(
        client,
        body(amount="20000.00"),
        params={"decision": "ALLOW", "risk_score": "0", "agent_recommendation": "ALLOW"},
        headers={"X-Agent-Recommendation": "ALLOW", "X-Policy-Decision": "ALLOW", "X-Override": "true"},
    )
    assert summary(data) == (60, "HIGH", "BLOCK")


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CAD", "AUD", "JPY"])
def test_currency_review_cannot_be_bypassed_by_client(client, currency):
    data = assess(
        client,
        body(currency=currency),
        params={"decision": "ALLOW"},
        headers={"X-Agent-Recommendation": "ALLOW"},
    )
    assert data["decision"] == "REVIEW"


def test_endpoint_has_no_inputs_besides_body():
    op = app.openapi()["paths"][URL]["post"]
    assert "parameters" not in op or op["parameters"] == []


# ---------------------------------------------------------- response schema


def test_response_matches_risk_assessment_schema(client):
    data = assess(client, body(amount="2500.00"))
    RiskAssessment.model_validate(data)
    assert set(data) == {"transaction", "risk_score", "risk_level", "decision", "reasons", "assessed_at"}
    assert data["transaction"] == {**CLEAN, "amount": "2500.00"}
    assert isinstance(data["risk_score"], int) and 0 <= data["risk_score"] <= 100
    assert data["risk_level"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert data["decision"] in {"ALLOW", "REVIEW", "BLOCK"}
    assert data["reasons"] and all(isinstance(r, str) and r for r in data["reasons"])


def test_assessed_at_is_utc_and_server_generated(client):
    before = datetime.now(UTC)
    data = assess(client, body())
    after = datetime.now(UTC)
    assert data["assessed_at"].endswith("Z")
    assessed_at = datetime.fromisoformat(data["assessed_at"])
    assert assessed_at.tzinfo is not None
    assert before <= assessed_at <= after


def test_response_content_type_is_json(client):
    assert client.post(URL, json=body()).headers["content-type"] == "application/json"


# -------------------------------------------------------------- determinism


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(body(), id="low"),
        pytest.param(body(currency="EUR", description="Urgent bitcoin", purpose="n/a"), id="non-usd-critical"),
        pytest.param(body(amount="1850.00", merchant_name="Unknown"), id="combined"),
    ],
)
def test_identical_requests_give_identical_results(client, payload):
    results = [assess(client, payload) for _ in range(10)]
    for result in results:
        result.pop("assessed_at")
    assert all(r == results[0] for r in results)


# ------------------------------------------------------------------ OpenAPI


def test_openapi_documents_endpoint():
    spec = app.openapi()
    op = spec["paths"][URL]["post"]
    assert op["operationId"] == "assess_risk"
    assert op["tags"] == ["risk"]
    assert op["summary"] == "Assess the risk of a proposed payment"
    assert op["requestBody"]["required"] is True

    request_schema = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert request_schema.startswith("#/components/schemas/TransactionDetails")
    response_schema = op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert response_schema == "#/components/schemas/RiskAssessment"
    assert "422" in op["responses"]

    examples = op["requestBody"]["content"]["application/json"]["examples"]
    assert {"low_allow", "medium_review", "non_usd_review", "critical_block"} <= set(examples)


def test_openapi_request_schema_has_only_transaction_fields():
    spec = app.openapi()
    ref = spec["paths"][URL]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    schema = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    assert set(schema["properties"]) == set(CLEAN)
    assert set(schema["required"]) == set(CLEAN)
    assert schema["additionalProperties"] is False


def test_openapi_examples_produce_documented_decisions(client):
    examples = app.openapi()["paths"][URL]["post"]["requestBody"]["content"]["application/json"]["examples"]
    expected = {"low_allow": "ALLOW", "medium_review": "REVIEW", "non_usd_review": "REVIEW", "critical_block": "BLOCK"}
    for name, decision in expected.items():
        assert assess(client, examples[name]["value"])["decision"] == decision


def test_swagger_ui_available(client):
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower()


def test_health_still_works_alongside_risk_route(client):
    assert client.get("/health").json() == {"status": "healthy", "service": "agentpay-guard-api"}
