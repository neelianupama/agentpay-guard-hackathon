"""Audit trail end to end (Step 7B): payments + risk -> audit events -> GET /api/v1/audit/events.

PayPal is FakeSandbox and Elastic is FakeElastic (httpx.MockTransport). No network.
"""

import json
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.routes.audit import get_audit_recorder
from app.api.routes.intent import get_intent_extractor
from app.api.routes.payments import get_orders_factory, get_payment_repository
from app.core.config import get_settings
from app.main import app
from app.services.audit import ALLOWED_FIELDS, AuditRecorder
from app.services.payments import InMemoryPaymentRepository
from tests.elastic_fake import FAKE_ELASTIC_KEY, FakeElastic
from tests.intent_fakes import FakeGroq, make_extractor, model_output
from tests.paypal_sandbox_fake import FAKE_ACCESS_TOKEN, FAKE_CLIENT_SECRET, FakeSandbox

PAY = "/api/v1/payments"
AUDIT = "/api/v1/audit/events"
REVIEWER_TOKEN = "FAKE-reviewer-token-" + "a" * 32
AUTH = {"Authorization": f"Bearer {REVIEWER_TOKEN}"}
INTENT_TEXT = "Pay 49.99 USD to Acme Cloud for hosting SECRET-INTENT-TEXT-MARKER"

ALLOW = {"merchant_name": "Acme Cloud", "amount": "49.99", "currency": "USD",
         "description": "Monthly hosting invoice", "purpose": "Infrastructure hosting"}
REVIEW = {**ALLOW, "amount": "2500.00"}
BLOCK = {**ALLOW, "amount": "3000.00", "description": "Urgent bitcoin purchase"}


@pytest.fixture
def sandbox():
    return FakeSandbox()


@pytest.fixture
def elastic():
    return FakeElastic()


@pytest.fixture
def recorder(elastic):
    return elastic.recorder()


@pytest.fixture
def repo():
    return InMemoryPaymentRepository()


@pytest.fixture
def api(sandbox, recorder, repo):
    service = sandbox.service()
    settings = replace(get_settings(), reviewer_token=REVIEWER_TOKEN)
    overrides = {
        get_payment_repository: lambda: repo,
        get_orders_factory: lambda: (lambda: service),
        get_settings: lambda: settings,
        get_audit_recorder: lambda: recorder,
    }
    app.dependency_overrides.update(overrides)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


def order_id(api, txn):
    """PayPal order ID via the reviewer view (buyer action responses are minimal)."""
    return api.get(f"{PAY}/{txn}", headers=AUTH).json()["paypal"]["order_id"]


def flush(api, recorder):
    api.portal.call(recorder.drain)


def audit(api, recorder, **params):
    flush(api, recorder)
    response = api.get(AUDIT, params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def types(events):
    return [e["event_type"] for e in reversed(events)]  # oldest first


# ================================================================= event recording


def test_risk_assessment_is_recorded(api, recorder):
    assert api.post("/api/v1/risk/assess", json=ALLOW).status_code == 200
    body = audit(api, recorder)
    (event,) = body["items"]
    assert event["event_type"] == "RISK_ASSESSED"
    assert (event["decision"], event["risk_score"], event["risk_level"]) == ("ALLOW", 0, "LOW")
    assert body["source"] == "elastic" and body["durable"] is True and body["degraded"] is False


def test_full_allow_flow_is_audited_in_order(api, recorder, sandbox):
    created = api.post(PAY, json=ALLOW).json()
    txn = created["transaction_id"]
    sandbox.buyer_approves(order_id(api, created["transaction_id"]))
    api.post(f"{PAY}/{txn}/authorize")
    api.post(f"{PAY}/{txn}/capture", headers=AUTH, json={"reviewer_name": "Asha", "comment": "ok"})

    body = audit(api, recorder, transaction_id=txn, limit=100)
    assert types(body["items"]) == [
        "PAYMENT_CREATED",
        "PAYMENT_STATUS_CHANGED",   # APPROVED_FOR_PAYMENT -> AWAITING_BUYER_APPROVAL
        "PAYPAL_ORDER_CREATED",
        "PAYMENT_STATUS_CHANGED",   # -> BUYER_APPROVED
        "PAYPAL_AUTHORIZATION",     # buyer approved (checked with PayPal)
        "PAYMENT_STATUS_CHANGED",   # -> AUTHORIZED
        "PAYPAL_AUTHORIZATION",     # authorized
        "PAYMENT_STATUS_CHANGED",   # -> PENDING_CAPTURE
        "PAYMENT_STATUS_CHANGED",   # -> CAPTURED
        "PAYPAL_CAPTURE",
    ]
    events = list(reversed(body["items"]))
    assert events[0]["decision"] == "ALLOW" and events[0]["reasons"]
    transitions = [(e.get("previous_status"), e["status"]) for e in events if e["event_type"] == "PAYMENT_STATUS_CHANGED"]
    assert transitions == [
        ("APPROVED_FOR_PAYMENT", "AWAITING_BUYER_APPROVAL"), ("AWAITING_BUYER_APPROVAL", "BUYER_APPROVED"),
        ("BUYER_APPROVED", "AUTHORIZED"), ("AUTHORIZED", "PENDING_CAPTURE"), ("PENDING_CAPTURE", "CAPTURED"),
    ]
    capture = events[-1]
    assert capture["outcome"] == "CAPTURED" and capture["paypal_status"] == "COMPLETED"
    assert capture["paypal_capture_id"] and capture["reviewer_name"] == "Asha"


def test_review_approval_rejection_and_block_are_recorded(api, recorder):
    approve_txn = api.post(PAY, json=REVIEW).json()["transaction_id"]
    reject_txn = api.post(PAY, json=REVIEW).json()["transaction_id"]
    block_txn = api.post(PAY, json=BLOCK).json()["transaction_id"]
    api.post(f"{PAY}/{approve_txn}/approve", headers=AUTH, json={"reviewer_name": "Asha", "comment": "Checked"})
    api.post(f"{PAY}/{reject_txn}/reject", headers=AUTH, json={"comment": "Unknown vendor"})

    approved = audit(api, recorder, event_type="REVIEW_APPROVED")["items"]
    rejected = audit(api, recorder, event_type="REVIEW_REJECTED")["items"]
    blocked = audit(api, recorder, transaction_id=block_txn)["items"]
    assert [(e["transaction_id"], e["reviewer_name"]) for e in approved] == [(approve_txn, "Asha")]
    assert [e["transaction_id"] for e in rejected] == [reject_txn]
    assert all("comment" not in e for e in approved + rejected)  # free text is never recorded
    assert [(e["event_type"], e["status"], e["decision"]) for e in blocked] == [("PAYMENT_CREATED", "BLOCKED", "BLOCK")]


def test_paypal_failures_record_safe_error_codes(api, recorder, sandbox):
    created = api.post(PAY, json=ALLOW).json()
    txn = created["transaction_id"]
    api.post(f"{PAY}/{txn}/authorize")  # buyer has not approved yet
    sandbox.buyer_approves(order_id(api, created["transaction_id"]))
    sandbox.inject("authorize", "lost")
    api.post(f"{PAY}/{txn}/authorize")

    events = audit(api, recorder, transaction_id=txn, limit=100)["items"]
    pending = [e for e in events if e.get("outcome") == "BUYER_APPROVAL_REQUIRED"]
    failed = [e for e in events if e["event_type"] == "PAYPAL_OPERATION_FAILED"]
    assert pending and pending[0]["paypal_status"] == "PAYER_ACTION_REQUIRED"
    assert [(e["operation"], e["error_code"], e["retry_safe"]) for e in failed] == [("AUTHORIZE", "PAYPAL_TIMEOUT", True)]


def test_void_is_recorded(api, recorder, sandbox):
    created = api.post(PAY, json=ALLOW).json()
    txn = created["transaction_id"]
    sandbox.buyer_approves(order_id(api, created["transaction_id"]))
    api.post(f"{PAY}/{txn}/authorize")
    api.post(f"{PAY}/{txn}/void", headers=AUTH, json={"comment": "Duplicate"})
    (void,) = audit(api, recorder, event_type="PAYPAL_VOID")["items"]
    assert (void["outcome"], void["status"]) == ("VOIDED", "VOIDED")
    assert "comment" not in void


def test_intent_parsing_is_not_indexed(api, recorder):
    fake = FakeGroq(content=model_output(merchant_name="Acme Cloud", amount="49.99"))
    app.dependency_overrides[get_intent_extractor] = lambda: make_extractor(fake)
    try:
        assert api.post("/api/v1/intent/parse", json={"request_text": INTENT_TEXT}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_intent_extractor, None)
    assert audit(api, recorder)["total"] == 0


# =================================================================== search API


def test_search_query_filters_and_pagination(api, recorder):
    for body in (ALLOW, REVIEW, BLOCK):
        api.post("/api/v1/risk/assess", json=body)
    all_events = audit(api, recorder, limit=100)
    assert all_events["total"] == 3

    page1 = audit(api, recorder, limit=2, offset=0)
    page2 = audit(api, recorder, limit=2, offset=2)
    assert len(page1["items"]) == 2 and len(page2["items"]) == 1
    assert {e["event_id"] for e in page1["items"]}.isdisjoint({e["event_id"] for e in page2["items"]})

    bitcoin = audit(api, recorder, q="bitcoin")
    assert [e["decision"] for e in bitcoin["items"]] == ["BLOCK"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"offset": 10_000}, {"event_type": "NOPE"},
     {"transaction_id": "bad id"}, {"q": "x" * 201}],
)
def test_search_rejects_invalid_parameters(api, params):
    assert api.get(AUDIT, params=params, headers=AUTH).status_code == 422


# ===================================================== unavailable Elastic / fallback


@pytest.mark.parametrize("fault", ["down", "timeout", "401", "500"])
def test_payments_succeed_when_elastic_is_unavailable(api, recorder, elastic, sandbox, fault, caplog):
    caplog.set_level(logging.DEBUG)
    elastic.inject(*[fault] * 100)

    created = api.post(PAY, json=ALLOW)
    assert created.status_code == 201
    txn = created.json()["transaction_id"]
    sandbox.buyer_approves(order_id(api, created.json()["transaction_id"]))
    assert api.post(f"{PAY}/{txn}/authorize").json()["status"] == "AUTHORIZED"
    assert api.post(f"{PAY}/{txn}/capture", headers=AUTH).json()["status"] == "CAPTURED"
    assert api.post("/api/v1/risk/assess", json=REVIEW).status_code == 200

    body = audit(api, recorder, limit=100)
    assert body["source"] == "memory_fallback"
    assert body["durable"] is False and body["degraded"] is True
    assert body["reason"].startswith("ELASTIC_")
    assert "not durably stored" in body["message"]
    assert body["total"] == body["pending_events"] >= 10
    assert "PAYPAL_CAPTURE" in {e["event_type"] for e in body["items"]}
    assert FAKE_ELASTIC_KEY not in caplog.text and FAKE_ELASTIC_KEY not in json.dumps(body)


def test_events_reach_elastic_after_recovery(api, recorder, elastic):
    # Outages for: the background flush, drain()'s best-effort retry, and the first search.
    elastic.inject("down", "down", "down")
    api.post("/api/v1/risk/assess", json=ALLOW)
    degraded = audit(api, recorder)
    assert degraded["source"] == "memory_fallback" and degraded["pending_events"] == 1

    # Elastic is reachable again: the held event is flushed (the in-between "Elastic results
    # plus pending events" state is covered in tests/services/test_audit.py).
    assert audit(api, recorder)["source"] == "elastic"

    api.post("/api/v1/risk/assess", json=BLOCK)
    recovered = audit(api, recorder)
    assert recovered["source"] == "elastic" and recovered["degraded"] is False
    assert recovered["total"] == 2 and recovered["pending_events"] == 0


def test_not_configured_state_is_explicit(api):
    app.dependency_overrides[get_audit_recorder] = lambda: AuditRecorder(None)
    api.post("/api/v1/risk/assess", json=ALLOW)
    body = api.get(AUDIT, headers=AUTH).json()
    assert (body["source"], body["durable"], body["degraded"], body["reason"]) == (
        "memory_fallback", False, True, "ELASTIC_NOT_CONFIGURED")


def test_audit_failure_inside_recorder_never_breaks_payments(api, recorder, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("audit exploded")

    monkeypatch.setattr(recorder, "emit", broken)
    assert api.post(PAY, json=REVIEW).status_code == 201
    assert api.post("/api/v1/risk/assess", json=ALLOW).status_code == 200


# ======================================================================= secrets


def test_indexed_documents_never_contain_secrets_or_request_ids(api, recorder, elastic, sandbox, repo):
    created = api.post(PAY, json=ALLOW).json()
    txn = created["transaction_id"]
    sandbox.buyer_approves(order_id(api, created["transaction_id"]))
    api.post(f"{PAY}/{txn}/authorize")
    api.post(f"{PAY}/{txn}/capture", headers=AUTH, json={"reviewer_name": "Asha"})
    flush(api, recorder)
    record = repo.get(txn)

    docs = elastic.stored()
    assert docs
    for doc in docs:
        assert set(doc) <= ALLOWED_FIELDS
    dump = json.dumps(docs) + api.get(AUDIT, params={"limit": 100}, headers=AUTH).text
    for secret in (REVIEWER_TOKEN, FAKE_ACCESS_TOKEN, FAKE_CLIENT_SECRET, FAKE_ELASTIC_KEY,
                   record.create_request_id, record.authorize_request_id, record.capture_request_id,
                   record.paypal_approval_url, "Bearer", "Monthly hosting invoice",
                   "Infrastructure hosting", "FAKE-raw-paypal"):
        assert secret not in dump, secret
    # Elastic received only the ApiKey header (no reviewer/PayPal auth forwarded).
    assert {r.headers["authorization"] for r in elastic.requests} == {f"ApiKey {FAKE_ELASTIC_KEY}"}


def test_openapi_documents_audit_endpoint(api):
    op = api.get("/openapi.json").json()["paths"]["/api/v1/audit/events"]["get"]
    assert op["operationId"] == "search_audit_events"
    assert {"q", "transaction_id", "event_type", "limit", "offset"} <= {p["name"] for p in op["parameters"]}


# ================================================================ authentication


@pytest.mark.parametrize(
    ("headers", "code"),
    [({}, "REVIEWER_AUTH_REQUIRED"),
     ({"Authorization": "Bearer wrong-token-" + "w" * 32}, "REVIEWER_AUTH_INVALID"),
     ({"Authorization": f"Basic {REVIEWER_TOKEN}"}, "REVIEWER_AUTH_REQUIRED"),
     ({"Authorization": REVIEWER_TOKEN}, "REVIEWER_AUTH_REQUIRED")],
)
def test_audit_search_requires_reviewer_token(api, recorder, headers, code):
    api.post(PAY, json=REVIEW)
    flush(api, recorder)
    response = api.get(AUDIT, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"code": code, "message": response.json()["message"]}
    assert response.headers["www-authenticate"] == "Bearer"
    assert "items" not in response.text and "txn_" not in response.text


def test_audit_token_in_query_string_is_not_accepted(api, recorder, caplog):
    caplog.set_level(logging.DEBUG)
    api.post(PAY, json=REVIEW)
    response = api.get(AUDIT, params={"token": REVIEWER_TOKEN, "access_token": REVIEWER_TOKEN,
                                      "reviewer_token": REVIEWER_TOKEN})
    assert response.status_code == 401
    # The application never logs it. (The test HTTP client logs its own request URL, and a
    # server access log would too: clients must never put the token in a URL.)
    app_logs = " ".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert REVIEWER_TOKEN not in app_logs


def test_audit_search_with_valid_token(api, recorder):
    api.post(PAY, json=REVIEW)
    body = audit(api, recorder)  # sends Authorization: Bearer <REVIEWER_TOKEN>
    assert body["total"] >= 1 and body["source"] == "elastic"


@pytest.mark.parametrize("reviewer_token", [None, "too-short"])
def test_audit_search_fails_closed_without_reviewer_configuration(api, reviewer_token):
    settings = replace(get_settings(), reviewer_token=reviewer_token)
    app.dependency_overrides[get_settings] = lambda: settings
    response = api.get(AUDIT, headers={"Authorization": "Bearer anything-" + "x" * 32})
    assert response.status_code == 503
    assert response.json()["code"] == "REVIEWER_AUTH_NOT_CONFIGURED"


def test_reviewer_comments_are_never_indexed_or_returned(api, recorder, elastic, sandbox):
    secret_comment = "FREE-TEXT-COMMENT-customer-phone-555-0100"
    review_txn = api.post(PAY, json=REVIEW).json()["transaction_id"]
    api.post(f"{PAY}/{review_txn}/approve", headers=AUTH,
             json={"reviewer_name": "Asha", "comment": secret_comment})
    created = api.post(PAY, json=ALLOW).json()
    txn = created["transaction_id"]
    sandbox.buyer_approves(order_id(api, created["transaction_id"]))
    api.post(f"{PAY}/{txn}/authorize")
    api.post(f"{PAY}/{txn}/capture", headers=AUTH, json={"comment": secret_comment})
    sandbox.inject("void", "reject:CANNOT_BE_VOIDED")

    body = audit(api, recorder, limit=100)
    searched = audit(api, recorder, q="555-0100")
    dump = json.dumps(elastic.stored()) + json.dumps(body)
    assert secret_comment not in dump
    assert all("comment" not in doc for doc in elastic.stored())
    assert searched["total"] == 0  # comment text is not searchable either
    # Reviewer accountability is kept: who acted is still recorded.
    assert any(e.get("reviewer_name") == "Asha" and e["event_type"] == "REVIEW_APPROVED"
               for e in body["items"])
