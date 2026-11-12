"""Payment workflow API (Step 4), end to end against the in-memory FakeSandbox.

No real or Sandbox network requests: PayPal is replaced via dependency
override with ``FakeSandbox`` (httpx.MockTransport).
"""

import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.api.routes.payments import get_orders_factory, get_payment_repository
from app.core.config import get_settings
from app.main import app
from app.services.payments import InMemoryPaymentRepository
from app.services.paypal import PayPalNotConfiguredError
from tests.paypal_sandbox_fake import (
    FAKE_ACCESS_TOKEN,
    FAKE_CLIENT_ID,
    FAKE_CLIENT_SECRET,
    FakeSandbox,
)

URL = "/api/v1/payments"
REVIEWER_TOKEN = "FAKE-reviewer-token-" + "r" * 32
AUTH = {"Authorization": f"Bearer {REVIEWER_TOKEN}"}
SECRETS = (REVIEWER_TOKEN, FAKE_ACCESS_TOKEN, FAKE_CLIENT_ID, FAKE_CLIENT_SECRET,
           "FAKE-raw-paypal-message", "FAKE-raw-paypal-detail")

ALLOW = {"merchant_name": "Acme Cloud", "amount": "49.99", "currency": "USD",
         "description": "Monthly hosting invoice", "purpose": "Infrastructure hosting"}
REVIEW = {**ALLOW, "amount": "2500.00"}                      # >= USD review threshold
BLOCK = {**ALLOW, "amount": "3000.00", "description": "Urgent bitcoin purchase"}


@pytest.fixture
def sandbox():
    return FakeSandbox()


@pytest.fixture
def repo():
    return InMemoryPaymentRepository()


@pytest.fixture
def reviewer_token():
    return REVIEWER_TOKEN


@pytest.fixture
def api(sandbox, repo, reviewer_token):
    service = sandbox.service()
    settings = replace(get_settings(), reviewer_token=reviewer_token)
    overrides = {
        get_payment_repository: lambda: repo,
        get_orders_factory: lambda: (lambda: service),
        get_settings: lambda: settings,
    }
    app.dependency_overrides.update(overrides)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        for dep in overrides:
            app.dependency_overrides.pop(dep, None)


ACTION_FIELDS = {"transaction_id", "status", "merchant_name", "amount", "currency",
                 "approval_url", "next_actions", "last_error"}
CLIENT_ACTIONS = {"create_order", "buyer_approves_on_paypal", "authorize"}


def assert_minimal(data):
    """Unauthenticated action responses: exactly the allowlisted minimal fields."""
    assert set(data) == ACTION_FIELDS, data
    assert set(data["next_actions"]) <= CLIENT_ACTIONS
    if data["last_error"] is not None:
        assert set(data["last_error"]) == {"code", "retry_safe"}


def full(api, txn):
    """The complete record, via the reviewer-authenticated read."""
    response = api.get(f"{URL}/{txn}", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def create(api, body, expected=201):
    """Create (unauthenticated, minimal response), then return the full reviewer view."""
    response = api.post(URL, json=body)
    assert response.status_code == expected, response.text
    data = response.json()
    if expected != 201:
        return data
    assert_minimal(data)
    return full(api, data["transaction_id"])


def act(api, txn, action, expected=200, headers=AUTH, json=None):
    response = api.post(f"{URL}/{txn}/{action}", headers=headers, json=json)
    assert response.status_code == expected, response.text
    data = response.json()
    if action in ("order", "authorize") and expected == 200:
        assert_minimal(data)  # buyer actions never return the full record
        return full(api, txn)
    return data


def authorized(api, sandbox):
    payment = create(api, ALLOW)
    sandbox.buyer_approves(payment["paypal"]["order_id"])
    data = act(api, payment["transaction_id"], "authorize", headers={})
    assert data["status"] == "AUTHORIZED"
    return data


def assert_no_secrets(*texts):
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, f"leaked {secret!r}"


# ======================================================================= create


def test_allow_creates_authorize_only_order(api, sandbox):
    data = create(api, ALLOW)

    assert data["status"] == "AWAITING_BUYER_APPROVAL"
    assert data["risk"]["decision"] == "ALLOW" and data["risk"]["risk_level"] == "LOW"
    order_id = data["paypal"]["order_id"]
    assert order_id in sandbox.orders
    assert data["paypal"]["approval_url"].startswith("https://www.sandbox.paypal.com/")
    assert data["paypal"]["authorization_id"] is None and data["paypal"]["capture_id"] is None
    assert data["next_actions"] == ["buyer_approves_on_paypal", "authorize"]
    (create_request,) = sandbox.api_requests("/v2/checkout/orders")
    import json as _json
    sent = _json.loads(create_request.content)
    assert sent["intent"] == "AUTHORIZE"
    assert sent["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "49.99"}
    assert sent["purchase_units"][0]["reference_id"] == data["transaction_id"]
    assert sandbox.counts == {"create": 1, "authorize": 0, "capture": 0, "void": 0}


def test_review_creates_no_paypal_request(api, sandbox):
    data = create(api, REVIEW)
    assert data["status"] == "PENDING_REVIEW"
    assert data["risk"]["decision"] == "REVIEW"
    assert data["paypal"]["order_id"] is None
    assert data["next_actions"] == ["approve", "reject"]
    assert sandbox.requests == []  # not even an OAuth token request


def test_block_creates_no_paypal_request(api, sandbox):
    data = create(api, BLOCK)
    assert data["status"] == "BLOCKED"
    assert data["risk"]["decision"] == "BLOCK"
    assert data["next_actions"] == []
    assert sandbox.requests == []


@pytest.mark.parametrize("amount", ["100.00", "200000.00"])  # INR ALLOW and INR REVIEW
def test_inr_never_reaches_paypal(api, sandbox, amount):
    response = api.post(URL, json={**ALLOW, "currency": "INR", "amount": amount})
    assert response.status_code == 422
    assert response.json()["code"] == "PAYPAL_CURRENCY_NOT_SUPPORTED"
    assert "never converted" in response.json()["message"]
    assert sandbox.requests == []
    assert api.get(URL, headers=AUTH).json()["total"] == 0


def test_inr_block_is_recorded_without_paypal(api, sandbox):
    data = create(api, {**ALLOW, "currency": "INR", "amount": "750000.00"})
    assert data["status"] == "BLOCKED" and data["currency"] == "INR"
    assert sandbox.requests == []


@pytest.mark.parametrize(
    "extra",
    [{"risk_score": 0}, {"decision": "ALLOW"}, {"risk_level": "LOW"}, {"reasons": []},
     {"status": "AUTHORIZED"}, {"approved": True}, {"paypal_order_id": "ORD00000000000001"}],
)
def test_client_supplied_risk_or_status_rejected(api, sandbox, extra):
    response = api.post(URL, json={**BLOCK, **extra})
    assert response.status_code == 422
    assert sandbox.requests == []
    assert api.get(URL, headers=AUTH).json()["total"] == 0


def test_create_lost_response_keeps_payment_and_retry_reuses_request_id(api, sandbox):
    sandbox.inject("create", "lost")  # PayPal created the order but the reply was lost
    response = api.post(URL, json=ALLOW)
    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "PAYPAL_TIMEOUT"
    assert body["details"]["retry_safe"] is True
    txn = body["details"]["transaction_id"]

    stored = api.get(f"{URL}/{txn}", headers=AUTH).json()
    assert stored["status"] == "APPROVED_FOR_PAYMENT"
    assert stored["last_error"]["retry_safe"] is True
    assert stored["next_actions"] == ["create_order"]

    data = act(api, txn, "order", headers={})
    assert data["status"] == "AWAITING_BUYER_APPROVAL"
    assert data["last_error"] is None
    creates = sandbox.api_requests("/v2/checkout/orders")
    assert len(creates) == 2
    assert creates[0].headers["paypal-request-id"] == creates[1].headers["paypal-request-id"]
    assert sandbox.counts["create"] == 1  # no duplicate order


def test_create_when_paypal_not_configured(api, sandbox):
    def not_configured():
        raise PayPalNotConfiguredError()

    app.dependency_overrides[get_orders_factory] = lambda: not_configured
    response = api.post(URL, json=ALLOW)
    assert response.status_code == 503
    assert response.json()["code"] == "PAYPAL_NOT_CONFIGURED"
    txn = response.json()["details"]["transaction_id"]
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "APPROVED_FOR_PAYMENT"
    # BLOCK and REVIEW still work without PayPal.
    assert create(api, REVIEW)["status"] == "PENDING_REVIEW"


def test_definitive_create_rejection_fails_payment(api, sandbox):
    sandbox.inject("create", "reject:CURRENCY_NOT_SUPPORTED")
    response = api.post(URL, json=ALLOW)
    assert response.status_code == 422
    assert response.json()["details"]["retry_safe"] is False
    txn = response.json()["details"]["transaction_id"]
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "FAILED"
    act(api, txn, "order", expected=409, headers={})


# ================================================================ reviewer auth


@pytest.mark.parametrize(
    ("headers", "code"),
    [({}, "REVIEWER_AUTH_REQUIRED"),
     ({"Authorization": "Bearer wrong-token-" + "x" * 32}, "REVIEWER_AUTH_INVALID"),
     ({"Authorization": f"Bearer {REVIEWER_TOKEN}x"}, "REVIEWER_AUTH_INVALID"),
     ({"Authorization": f"Basic {REVIEWER_TOKEN}"}, "REVIEWER_AUTH_REQUIRED"),
     ({"Authorization": REVIEWER_TOKEN}, "REVIEWER_AUTH_REQUIRED")],
)
@pytest.mark.parametrize("action", ["approve", "reject", "capture", "void"])
def test_invalid_reviewer_token_rejected(api, sandbox, headers, code, action):
    txn = create(api, REVIEW)["transaction_id"]
    response = api.post(f"{URL}/{txn}/{action}", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == code
    assert response.headers["www-authenticate"] == "Bearer"
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "PENDING_REVIEW"
    assert sandbox.requests == []


def test_reviewer_token_not_accepted_in_query_or_body(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    r1 = api.post(f"{URL}/{txn}/approve?token={REVIEWER_TOKEN}&reviewer_token={REVIEWER_TOKEN}")
    r2 = api.post(f"{URL}/{txn}/approve", json={"reviewer_token": REVIEWER_TOKEN})
    assert r1.status_code == 401
    assert r2.status_code in (401, 422)
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "PENDING_REVIEW"
    assert sandbox.requests == []


@pytest.mark.parametrize("reviewer_token", [None, "too-short-token"])
def test_missing_reviewer_configuration_fails_closed(api, sandbox, repo, reviewer_token):
    txn = api.post(URL, json=REVIEW).json()["transaction_id"]  # reviewer reads are disabled here
    for action in ("approve", "reject", "capture", "void"):
        response = api.post(f"{URL}/{txn}/{action}", headers={"Authorization": "Bearer anything"})
        assert response.status_code == 503
        assert response.json()["code"] == "REVIEWER_AUTH_NOT_CONFIGURED"
    for path in (URL, f"{URL}/{txn}"):  # reads fail closed too
        response = api.get(path, headers={"Authorization": "Bearer anything"})
        assert response.status_code == 503
        assert response.json()["code"] == "REVIEWER_AUTH_NOT_CONFIGURED"
    assert repo.get(txn).status == "PENDING_REVIEW"
    assert sandbox.requests == []


def test_reviewer_token_never_logged_stored_or_returned(api, sandbox, repo, caplog):
    caplog.set_level(logging.DEBUG)
    txn = create(api, REVIEW)["transaction_id"]
    approved = act(api, txn, "approve", json={"reviewer_name": "Asha", "comment": "Invoice checked"})
    bad = api.post(f"{URL}/{txn}/capture", headers={"Authorization": "Bearer nope-" + "n" * 32})
    listing = api.get(URL, headers=AUTH)
    assert_no_secrets(caplog.text, str(approved), bad.text, listing.text, repr(repo.get(txn)))


# ================================================================ review workflow


def test_review_order_created_only_after_reviewer_approval(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    assert sandbox.requests == []

    data = act(api, txn, "approve", json={"reviewer_name": "Asha", "comment": "Invoice checked"})

    assert data["status"] == "AWAITING_BUYER_APPROVAL"
    assert data["paypal"]["order_id"] in sandbox.orders
    assert data["review"]["decision"] == "APPROVE"
    assert data["review"]["reviewer_name"] == "Asha"
    assert data["review"]["comment"] == "Invoice checked"
    assert [a["action"] for a in data["reviewer_actions"]] == ["approve"]
    assert sandbox.counts["create"] == 1


def test_reject_then_approve_not_allowed(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    assert act(api, txn, "reject", json={"comment": "Unknown vendor"})["status"] == "REJECTED"
    body = act(api, txn, "approve", expected=409)
    assert body["code"] == "PAYMENT_INVALID_STATE"
    assert sandbox.requests == []


def test_blocked_cannot_be_approved(api, sandbox):
    txn = create(api, BLOCK)["transaction_id"]
    assert act(api, txn, "approve", expected=409)["code"] == "PAYMENT_INVALID_STATE"
    assert act(api, txn, "order", expected=409, headers={})["code"] == "PAYMENT_INVALID_STATE"
    assert act(api, txn, "authorize", expected=409, headers={})["code"] == "PAYMENT_INVALID_STATE"
    assert sandbox.requests == []


def test_approve_twice_creates_one_order(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    act(api, txn, "approve")
    act(api, txn, "approve", expected=409)
    assert sandbox.counts["create"] == 1


def test_approval_body_rejects_unknown_fields(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    response = api.post(f"{URL}/{txn}/approve", headers=AUTH, json={"decision": "ALLOW"})
    assert response.status_code == 422
    assert sandbox.requests == []


# ================================================================== authorization


def test_authorize_requires_paypal_approved_status(api, sandbox):
    payment = create(api, ALLOW)
    txn = payment["transaction_id"]

    body = act(api, txn, "authorize", expected=409, headers={})
    assert body["code"] == "PAYMENT_BUYER_APPROVAL_REQUIRED"
    assert body["details"]["paypal_order_status"] == "PAYER_ACTION_REQUIRED"
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "AWAITING_BUYER_APPROVAL"
    assert sandbox.counts["authorize"] == 0
    assert sandbox.api_requests("/authorize") == []  # never attempted


def test_buyer_approval_flag_from_frontend_is_ignored(api, sandbox):
    txn = create(api, ALLOW)["transaction_id"]
    response = api.post(f"{URL}/{txn}/authorize",
                        json={"buyer_approved": True, "status": "APPROVED", "paypal_status": "APPROVED"})
    assert response.status_code == 409
    assert response.json()["code"] == "PAYMENT_BUYER_APPROVAL_REQUIRED"
    assert sandbox.counts["authorize"] == 0


def test_authorize_after_buyer_approval_on_paypal(api, sandbox):
    payment = create(api, ALLOW)
    sandbox.buyer_approves(payment["paypal"]["order_id"])

    data = act(api, payment["transaction_id"], "authorize", headers={})

    assert data["status"] == "AUTHORIZED"
    assert data["paypal"]["authorization_id"] in sandbox.authorizations
    assert data["paypal"]["authorization_status"] == "CREATED"
    assert data["next_actions"] == ["capture", "void"]
    assert sandbox.counts["capture"] == 0  # never captured automatically


def test_pending_authorization_is_preserved_then_refreshed(api, sandbox):
    sandbox.next_authorization_status = "PENDING"
    payment = create(api, ALLOW)
    txn = payment["transaction_id"]
    sandbox.buyer_approves(payment["paypal"]["order_id"])

    data = act(api, txn, "authorize", headers={})
    assert data["status"] == "AUTHORIZATION_PENDING"
    assert act(api, txn, "capture", expected=409)["code"] == "PAYMENT_INVALID_STATE"
    assert act(api, txn, "authorize", headers={})["status"] == "AUTHORIZATION_PENDING"

    sandbox.set_authorization_status(data["paypal"]["authorization_id"], "CREATED")
    assert act(api, txn, "authorize", headers={})["status"] == "AUTHORIZED"
    assert sandbox.counts["authorize"] == 1  # refreshed via GET, not re-authorized


def test_authorize_lost_response_retry_reuses_request_id(api, sandbox):
    payment = create(api, ALLOW)
    txn = payment["transaction_id"]
    sandbox.buyer_approves(payment["paypal"]["order_id"])
    sandbox.inject("authorize", "lost")

    body = act(api, txn, "authorize", expected=504, headers={})
    assert body["details"] == {"transaction_id": txn, "status": "BUYER_APPROVED", "retry_safe": True}

    assert act(api, txn, "authorize", headers={})["status"] == "AUTHORIZED"
    attempts = sandbox.api_requests("/authorize")
    assert len(attempts) == 2
    assert attempts[0].headers["paypal-request-id"] == attempts[1].headers["paypal-request-id"]
    assert sandbox.counts["authorize"] == 1


def test_denied_authorization_fails_payment(api, sandbox):
    sandbox.next_authorization_status = "DENIED"
    payment = create(api, ALLOW)
    sandbox.buyer_approves(payment["paypal"]["order_id"])
    body = act(api, payment["transaction_id"], "authorize", expected=422, headers={})
    assert body["code"] == "PAYPAL_PAYMENT_DECLINED"
    assert api.get(f"{URL}/{payment['transaction_id']}", headers=AUTH).json()["status"] == "FAILED"


def test_paypal_amount_mismatch_does_not_authorize(api, sandbox):
    payment = create(api, ALLOW)
    sandbox.buyer_approves(payment["paypal"]["order_id"])
    sandbox.order_amount_override = {"currency_code": "USD", "value": "4999.00"}
    body = act(api, payment["transaction_id"], "authorize", expected=502, headers={})
    assert body["code"] == "PAYPAL_BAD_RESPONSE"
    assert api.get(f"{URL}/{payment['transaction_id']}", headers=AUTH).json()["status"] == "AWAITING_BUYER_APPROVAL"
    assert sandbox.counts["authorize"] == 0


# ======================================================================== capture


def test_capture_requires_reviewer_and_authorized_status(api, sandbox):
    review_txn = create(api, REVIEW)["transaction_id"]
    allow = create(api, ALLOW)
    act(api, review_txn, "capture", expected=409)
    act(api, allow["transaction_id"], "capture", expected=409)  # awaiting buyer approval
    act(api, allow["transaction_id"], "capture", expected=401, headers={})
    assert sandbox.counts["capture"] == 0


def test_capture_completed(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]

    data = act(api, txn, "capture", json={"reviewer_name": "Asha"})

    assert data["status"] == "CAPTURED"
    assert data["paypal"]["capture_status"] == "COMPLETED"
    assert data["paypal"]["capture_id"] in sandbox.captures
    assert data["reviewer_actions"][-1]["action"] == "capture"
    assert data["next_actions"] == []
    (capture_request,) = sandbox.api_requests("/capture")
    import json as _json
    assert _json.loads(capture_request.content) == {
        "amount": {"currency_code": "USD", "value": "49.99"}, "final_capture": True}

    assert act(api, txn, "capture", expected=409)["code"] == "PAYMENT_INVALID_STATE"
    assert sandbox.counts["capture"] == 1


def test_capture_pending_is_not_captured(api, sandbox):
    sandbox.next_capture_status = "PENDING"
    txn = authorized(api, sandbox)["transaction_id"]
    data = act(api, txn, "capture")
    assert data["status"] == "CAPTURE_PENDING"
    assert data["paypal"]["capture_status"] == "PENDING"
    act(api, txn, "capture", expected=409)
    act(api, txn, "void", expected=409)
    assert sandbox.counts["capture"] == 1


@pytest.mark.parametrize("fault", ["lost", "timeout", "unavailable"])
def test_capture_ambiguous_failure_retries_with_same_request_id(api, sandbox, fault):
    txn = authorized(api, sandbox)["transaction_id"]
    sandbox.inject("capture", fault)

    body = act(api, txn, "capture", expected=504 if fault != "unavailable" else 503)
    assert body["details"]["status"] == "PENDING_CAPTURE"
    assert body["details"]["retry_safe"] is True
    stored = api.get(f"{URL}/{txn}", headers=AUTH).json()
    assert stored["status"] == "PENDING_CAPTURE"
    assert stored["next_actions"] == ["capture"]
    act(api, txn, "void", expected=409)  # cannot void while capture outcome is unknown

    assert act(api, txn, "capture")["status"] == "CAPTURED"
    attempts = sandbox.api_requests("/capture")
    assert len(attempts) == 2
    assert attempts[0].headers["paypal-request-id"] == attempts[1].headers["paypal-request-id"]
    assert sandbox.counts["capture"] == 1  # never captured twice


def test_capture_declined_is_failed(api, sandbox):
    sandbox.next_capture_status = "DECLINED"
    txn = authorized(api, sandbox)["transaction_id"]
    body = act(api, txn, "capture", expected=422)
    assert body["code"] == "PAYPAL_PAYMENT_DECLINED"
    assert body["details"]["retry_safe"] is False
    assert api.get(f"{URL}/{txn}", headers=AUTH).json()["status"] == "FAILED"


def test_repeated_capture_requests_capture_once(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]
    results = [api.post(f"{URL}/{txn}/capture", headers=AUTH).status_code for _ in range(5)]
    assert results == [200, 409, 409, 409, 409]
    assert sandbox.counts["capture"] == 1


# =========================================================================== void


def test_void_authorized_payment(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]
    data = act(api, txn, "void", json={"comment": "Duplicate invoice"})
    assert data["status"] == "VOIDED"
    assert data["paypal"]["authorization_status"] == "VOIDED"
    act(api, txn, "void", expected=409)
    act(api, txn, "capture", expected=409)
    assert sandbox.counts == {"create": 1, "authorize": 1, "capture": 0, "void": 1}


def test_no_void_after_capture(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]
    act(api, txn, "capture")
    assert act(api, txn, "void", expected=409)["code"] == "PAYMENT_INVALID_STATE"
    assert sandbox.api_requests("/void") == []


def test_void_before_authorization_not_allowed(api, sandbox):
    txn = create(api, ALLOW)["transaction_id"]
    act(api, txn, "void", expected=409)
    assert sandbox.api_requests("/void") == []


def test_void_lost_response_retry(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]
    sandbox.inject("void", "lost")
    assert act(api, txn, "void", expected=504)["details"]["status"] == "PENDING_VOID"
    act(api, txn, "capture", expected=409)
    assert act(api, txn, "void")["status"] == "VOIDED"
    attempts = sandbox.api_requests("/void")
    assert attempts[0].headers["paypal-request-id"] == attempts[1].headers["paypal-request-id"]
    assert sandbox.counts["void"] == 1


def test_void_previously_voided_is_voided(api, sandbox):
    txn = authorized(api, sandbox)["transaction_id"]
    sandbox.inject("void", "reject:PREVIOUSLY_VOIDED")
    assert act(api, txn, "void")["status"] == "VOIDED"


# ========================================================================== reads


def test_get_and_list_with_pagination(api, sandbox):
    ids = [create(api, REVIEW)["transaction_id"] for _ in range(5)]

    page = api.get(URL, params={"limit": 2}, headers=AUTH).json()
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 0
    assert [p["transaction_id"] for p in page["items"]] == ids[::-1][:2]  # newest first
    last = api.get(URL, params={"limit": 2, "offset": 4}, headers=AUTH).json()
    assert [p["transaction_id"] for p in last["items"]] == [ids[0]]

    one = api.get(f"{URL}/{ids[2]}", headers=AUTH).json()
    assert one["transaction_id"] == ids[2]
    assert one["risk"]["reasons"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
def test_list_rejects_bad_pagination(api, params):
    assert api.get(URL, params=params, headers=AUTH).status_code == 422


def test_get_unknown_and_invalid_ids(api):
    assert api.get(f"{URL}/txn_doesnotexist", headers=AUTH).status_code == 404
    assert api.get(f"{URL}/txn_doesnotexist", headers=AUTH).json()["code"] == "PAYMENT_NOT_FOUND"
    assert api.get(f"{URL}/{'x' * 65}", headers=AUTH).status_code == 422
    assert api.post(f"{URL}/bad%20id/authorize").status_code == 422


def test_read_endpoints_do_not_leak_secrets_or_request_ids(api, sandbox, repo):
    txn = authorized(api, sandbox)["transaction_id"]
    act(api, txn, "capture")
    one = api.get(f"{URL}/{txn}", headers=AUTH)
    listing = api.get(URL, headers=AUTH)
    record = repo.get(txn)

    assert_no_secrets(one.text, listing.text)
    for request_id in (record.create_request_id, record.authorize_request_id,
                       record.capture_request_id):
        assert request_id and request_id not in one.text and request_id not in listing.text
    assert "Bearer" not in one.text
    assert set(one.json()) == {
        "transaction_id", "status", "merchant_name", "amount", "currency", "description",
        "purpose", "risk", "paypal", "review", "reviewer_actions", "last_error",
        "next_actions", "created_at", "updated_at"}
    assert set(one.json()["paypal"]) == {
        "order_id", "approval_url", "order_status", "authorization_id",
        "authorization_status", "capture_id", "capture_status"}


def test_errors_do_not_expose_raw_paypal_text(api, sandbox, caplog):
    caplog.set_level(logging.DEBUG)
    txn = authorized(api, sandbox)["transaction_id"]
    sandbox.inject("capture", "reject:AUTHORIZATION_EXPIRED")
    response = api.post(f"{URL}/{txn}/capture", headers=AUTH)
    assert response.status_code == 422
    assert response.json()["code"] == "PAYPAL_REQUEST_REJECTED"
    assert response.json()["message"] == "This authorization has expired."
    assert_no_secrets(response.text, caplog.text, api.get(f"{URL}/{txn}", headers=AUTH).text)


# ======================================================= read endpoint authentication


READ_AUTH_FAILURES = [
    ({}, "REVIEWER_AUTH_REQUIRED"),
    ({"Authorization": "Bearer wrong-token-" + "w" * 32}, "REVIEWER_AUTH_INVALID"),
    ({"Authorization": f"Bearer {REVIEWER_TOKEN}x"}, "REVIEWER_AUTH_INVALID"),
    ({"Authorization": f"Basic {REVIEWER_TOKEN}"}, "REVIEWER_AUTH_REQUIRED"),
    ({"Authorization": REVIEWER_TOKEN}, "REVIEWER_AUTH_REQUIRED"),
]


@pytest.mark.parametrize(("headers", "code"), READ_AUTH_FAILURES)
@pytest.mark.parametrize("which", ["list", "detail"])
def test_payment_reads_require_reviewer_token(api, headers, code, which):
    txn = act(api, create(api, REVIEW)["transaction_id"], "approve",
              json={"reviewer_name": "Asha", "comment": "SENSITIVE-REVIEW-COMMENT"})["transaction_id"]
    path = URL if which == "list" else f"{URL}/{txn}"

    response = api.get(path, headers=headers)

    assert response.status_code == 401
    assert response.json()["code"] == code
    assert response.headers["www-authenticate"] == "Bearer"
    for leaked in (txn, "SENSITIVE-REVIEW-COMMENT", "Asha", "Acme Cloud", "Monthly hosting invoice"):
        assert leaked not in response.text


@pytest.mark.parametrize("which", ["list", "detail"])
def test_payment_reads_ignore_token_in_query_string(api, which):
    txn = create(api, REVIEW)["transaction_id"]
    path = URL if which == "list" else f"{URL}/{txn}"
    response = api.get(path, params={"token": REVIEWER_TOKEN, "access_token": REVIEWER_TOKEN})
    assert response.status_code == 401
    assert txn not in response.text


def test_payment_reads_with_valid_token_return_unchanged_schema(api):
    txn = create(api, REVIEW)["transaction_id"]
    listing = api.get(URL, headers=AUTH)
    one = api.get(f"{URL}/{txn}", headers=AUTH)
    assert listing.status_code == one.status_code == 200
    assert set(listing.json()) == {"items", "total", "limit", "offset"}
    assert listing.json()["items"][0]["transaction_id"] == txn
    assert one.json()["transaction_id"] == txn and one.json()["status"] == "PENDING_REVIEW"


def test_unauthenticated_client_actions_still_work(api, sandbox):
    """Create, order retry and authorize stay usable without the reviewer token."""
    created = api.post(URL, json=ALLOW)
    assert created.status_code == 201
    txn = created.json()["transaction_id"]
    sandbox.buyer_approves(full(api, txn)["paypal"]["order_id"])
    authorized = api.post(f"{URL}/{txn}/authorize")
    assert authorized.status_code == 200 and authorized.json()["status"] == "AUTHORIZED"


def test_openapi_marks_reads_as_reviewer_only(api):
    paths = api.get("/openapi.json").json()["paths"]
    for path in ("/api/v1/payments", "/api/v1/payments/{transaction_id}"):
        op = paths[path]["get"]
        assert "Authorization: Bearer" in op["description"]
        assert "401" in op["responses"]


# ============================================ minimal responses for buyer actions


SENSITIVE_KEYS = {"risk", "risk_score", "risk_level", "decision", "reasons", "review",
                  "reviewer_actions", "reviewer_name", "comment", "description", "purpose",
                  "paypal", "order_id", "authorization_id", "capture_id", "created_at",
                  "updated_at", "message"}


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_create_returns_only_minimal_fields(api, sandbox):
    for body, status in ((ALLOW, "AWAITING_BUYER_APPROVAL"), (REVIEW, "PENDING_REVIEW"), (BLOCK, "BLOCKED")):
        data = api.post(URL, json=body).json()
        assert_minimal(data)
        assert data["status"] == status
        assert not SENSITIVE_KEYS & set(_keys(data))
    allow = api.post(URL, json=ALLOW).json()
    assert allow["approval_url"].startswith("https://www.sandbox.paypal.com/")
    assert allow["next_actions"] == ["buyer_approves_on_paypal", "authorize"]
    review = api.post(URL, json=REVIEW).json()
    assert review["approval_url"] is None and review["next_actions"] == []  # approve/reject hidden


def test_authorize_and_order_responses_never_expose_reviewer_or_risk_data(api, sandbox):
    """A reviewer-approved payment: unauthenticated actions must not reveal the review."""
    txn = create(api, REVIEW)["transaction_id"]
    act(api, txn, "approve", json={"reviewer_name": "Asha Reviewer", "comment": "SECRET-REVIEW-NOTE"})
    reviewer_view = full(api, txn)
    assert reviewer_view["review"]["comment"] == "SECRET-REVIEW-NOTE"  # reviewers still see it

    sandbox.buyer_approves(reviewer_view["paypal"]["order_id"])
    response = api.post(f"{URL}/{txn}/authorize")
    assert response.status_code == 200
    data = response.json()
    assert_minimal(data)
    assert data["status"] == "AUTHORIZED" and data["approval_url"] is None
    assert data["next_actions"] == []  # capture/void are reviewer actions, not listed
    authorization_id = full(api, txn)["paypal"]["authorization_id"]
    assert authorization_id
    for leaked in ("Asha Reviewer", "SECRET-REVIEW-NOTE", reviewer_view["paypal"]["order_id"],
                   authorization_id, "Monthly hosting invoice",
                   "Infrastructure hosting", "human-review threshold", "CREATED"):
        assert leaked not in response.text, leaked
    assert not SENSITIVE_KEYS & set(_keys(data))


def test_order_retry_returns_minimal_view(api, sandbox):
    sandbox.inject("create", "lost")
    txn = api.post(URL, json=ALLOW).json()["details"]["transaction_id"]
    response = api.post(f"{URL}/{txn}/order")
    assert response.status_code == 200
    data = response.json()
    assert_minimal(data)
    assert data["status"] == "AWAITING_BUYER_APPROVAL" and data["approval_url"]
    assert data["last_error"] is None
    assert not SENSITIVE_KEYS & set(_keys(data))


def test_minimal_view_reports_last_error_code_only(api, sandbox, repo):
    from app.api.routes.payments import to_action_response

    sandbox.inject("create", "lost")
    txn = api.post(URL, json=ALLOW).json()["details"]["transaction_id"]
    record = repo.get(txn)
    assert record.last_error is not None and record.last_error.message  # stored internally

    data = to_action_response(record).model_dump(mode="json")
    assert data["status"] == "APPROVED_FOR_PAYMENT"
    assert data["last_error"] == {"code": "PAYPAL_TIMEOUT", "retry_safe": True}
    assert data["next_actions"] == ["create_order"]
    assert record.last_error.message not in str(data)


def test_error_responses_of_buyer_actions_expose_only_safe_details(api, sandbox):
    txn = create(api, ALLOW)["transaction_id"]
    response = api.post(f"{URL}/{txn}/authorize")  # buyer has not approved yet
    assert response.status_code == 409
    body = response.json()
    assert set(body) == {"code", "message", "details"}
    assert set(body["details"]) <= {"transaction_id", "status", "retry_safe", "paypal_order_status"}
    assert_no_secrets(response.text)


def test_reviewer_endpoints_still_return_the_full_record(api, sandbox):
    txn = create(api, REVIEW)["transaction_id"]
    data = act(api, txn, "approve", json={"reviewer_name": "Asha", "comment": "ok"})
    assert data["review"]["reviewer_name"] == "Asha" and data["review"]["comment"] == "ok"
    assert "risk" in data and "paypal" in data and "reviewer_actions" in data


def test_openapi_documents_minimal_action_responses(api):
    paths = api.get("/openapi.json").json()["paths"]
    def ref(path, code):
        return paths[path]["post"]["responses"][code]["content"]["application/json"]["schema"]["$ref"]
    assert ref("/api/v1/payments", "201").endswith("/PaymentActionResponse")
    assert ref("/api/v1/payments/{transaction_id}/order", "200").endswith("/PaymentActionResponse")
    assert ref("/api/v1/payments/{transaction_id}/authorize", "200").endswith("/PaymentActionResponse")
    assert ref("/api/v1/payments/{transaction_id}/capture", "200").endswith("/PaymentRecordResponse")
