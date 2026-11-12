"""Audit events, Elastic REST client and recorder (Step 7B). Elastic is FakeElastic only."""

import asyncio
import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import ConfigurationError, load_settings
from app.schemas import TransactionDetails
from app.services.audit import (
    ALLOWED_FIELDS,
    INDEX_MAPPING,
    AuditEvent,
    AuditEventType,
    AuditRecorder,
    ElasticError,
    risk_assessed,
)
from app.services.risk import RiskEngine
from tests.elastic_fake import FAKE_ELASTIC_KEY, FAKE_INDEX, FakeElastic, compatible_mapping

E = AuditEventType
DETAILS = TransactionDetails(merchant_name="Acme Cloud", amount=Decimal("49.99"), currency="USD",
                             description="Monthly hosting invoice", purpose="Infrastructure hosting")


def run(coro):
    return asyncio.run(coro)


def event(n: int = 0, **kwargs) -> AuditEvent:
    base = {"event_type": E.PAYMENT_CREATED, "transaction_id": f"txn_{n:04d}",
            "occurred_at": datetime(2026, 11, 12, tzinfo=UTC) + timedelta(seconds=n),
            "merchant_name": "Acme Cloud", "amount": "49.99", "currency": "USD"}
    return AuditEvent(**{**base, **kwargs})


def no_secret(*texts: str) -> None:
    for text in texts:
        assert FAKE_ELASTIC_KEY not in text
        assert base64.b64encode(FAKE_ELASTIC_KEY.encode()).decode() not in text
        assert "FAKE-raw-elastic-error" not in text


# ==================================================================== event model


def test_allowlist_matches_the_strict_index_mapping():
    # 'comment' stays mapped only so an index created by the first schema still verifies.
    assert ALLOWED_FIELDS == set(INDEX_MAPPING["properties"]) - {"comment"}
    assert "comment" not in ALLOWED_FIELDS
    assert INDEX_MAPPING["dynamic"] == "strict"


@pytest.mark.parametrize(
    "field",
    ["request_text", "reviewer_token", "authorization", "access_token", "paypal_request_id",
     "create_request_id", "capture_request_id", "approval_url", "client_secret", "raw_response",
     "description", "purpose", "comment"],
)
def test_non_allowlisted_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        event(**{field: "x"})


@pytest.mark.parametrize(
    ("field", "value"),
    [("paypal_order_id", "agentpay-create-order-0123456789abcdef0123456789abcdef"),
     ("paypal_capture_id", "lowercase123456789"), ("error_code", "raw PayPal message"),
     ("amount", "1e5"), ("currency", "usd"), ("transaction_id", "txn 1"),
     ("risk_score", 101), ("event_id", "not-a-uuid")],
)
def test_field_values_are_constrained(field, value):
    with pytest.raises(ValidationError):
        event(**{field: value})


def test_document_drops_nulls_and_contains_only_allowlisted_fields():
    doc = event().to_document()
    assert set(doc) <= ALLOWED_FIELDS
    assert "reviewer_name" not in doc and "error_code" not in doc
    assert doc["amount"] == "49.99" and doc["schema_version"] == 1


def test_risk_assessed_event_excludes_description_and_purpose():
    assessment = RiskEngine().assess(DETAILS)
    doc = risk_assessed(DETAILS, assessment).to_document()
    assert doc["event_type"] == "RISK_ASSESSED" and doc["decision"] == "ALLOW"
    assert doc["reasons"] == assessment.reasons
    assert "Monthly hosting invoice" not in json.dumps(doc)
    assert "Infrastructure hosting" not in json.dumps(doc)


# ======================================================================== config


@pytest.mark.parametrize(
    "url",
    ["http://elastic.example.com", "https://user:pass@es.example.com", "https://es.example.com/?x=1",
     "https://es.example.com/#f", "ftp://es.example.com", "es.example.com", "https://es.example.com:99999"],
)
def test_invalid_elastic_urls_rejected_without_echo(url):
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings({"ELASTIC_URL": url})
    assert url not in str(exc_info.value) and "pass" not in str(exc_info.value)


@pytest.mark.parametrize(
    "url", ["https://my-deploy.es.us-east-1.aws.elastic.cloud", "https://es.example.com:9243/",
            "http://localhost:9200", "http://127.0.0.1:9200"],
)
def test_valid_elastic_urls(url):
    assert load_settings({"ELASTIC_URL": url}).elastic_url == url.rstrip("/")


def test_elastic_configured_requires_url_and_key():
    assert not load_settings({}).elastic_configured
    assert not load_settings({"ELASTIC_URL": "https://es.example.com"}).elastic_configured
    assert load_settings({"ELASTIC_URL": "https://es.example.com",
                          "ELASTIC_API_KEY": "k"}).elastic_configured


@pytest.mark.parametrize("index", ["Upper", "-bad", "_bad", ".hidden", "a b", "a/b", "x" * 101, "a..b"])
def test_invalid_index_names_rejected(index):
    with pytest.raises(ConfigurationError):
        load_settings({"ELASTIC_INDEX": index})


def test_index_and_timeout_defaults_and_validation():
    settings = load_settings({})
    assert settings.elastic_index == "agentpay-audit-events"
    assert settings.elastic_timeout_seconds == 5.0
    assert load_settings({"ELASTIC_TIMEOUT_SECONDS": "12"}).elastic_timeout_seconds == 12.0
    with pytest.raises(ConfigurationError):
        load_settings({"ELASTIC_TIMEOUT_SECONDS": "0"})


def test_elastic_key_not_in_settings_repr():
    settings = load_settings({"ELASTIC_URL": "https://es.example.com", "ELASTIC_API_KEY": FAKE_ELASTIC_KEY})
    assert FAKE_ELASTIC_KEY not in repr(settings)


# ================================================================ Elastic client


def test_creates_index_with_strict_mapping_and_api_key_header():
    fake = FakeElastic()
    run(fake.client().index_event(event()))

    methods = [(r.method, r.url.path) for r in fake.requests]
    assert methods == [("HEAD", f"/{FAKE_INDEX}"), ("PUT", f"/{FAKE_INDEX}"),
                       ("PUT", f"/{FAKE_INDEX}/_create/{event().event_id}")][:2] + methods[2:]
    assert json.loads(fake.requests[1].content) == {"mappings": INDEX_MAPPING}
    assert all(r.headers["authorization"] == f"ApiKey {FAKE_ELASTIC_KEY}" for r in fake.requests)
    assert len(fake.stored()) == 1


def test_existing_compatible_index_is_verified_not_modified():
    fake = FakeElastic(existing_mapping=compatible_mapping())
    run(fake.client().index_event(event()))
    assert [(r.method, r.url.path.split("/")[-1]) for r in fake.requests][:2] == [
        ("HEAD", FAKE_INDEX), ("GET", "_mapping")]
    assert not any(r.method == "PUT" and r.url.path == f"/{FAKE_INDEX}" for r in fake.requests)


def test_incompatible_existing_mapping_is_reported_not_overwritten():
    mapping = compatible_mapping()
    mapping["properties"]["amount"] = {"type": "float"}
    fake = FakeElastic(existing_mapping=mapping)
    with pytest.raises(ElasticError) as exc_info:
        run(fake.client().index_event(event()))
    assert exc_info.value.code == "ELASTIC_MAPPING_MISMATCH"
    assert fake.indices[FAKE_INDEX]["properties"]["amount"] == {"type": "float"}
    assert fake.stored() == []


def test_index_event_is_idempotent_by_event_id():
    fake = FakeElastic()
    client = fake.client()
    e = event()

    async def scenario():
        await client.index_event(e)
        await client.index_event(e)  # 409 -> treated as already stored

    run(scenario())
    assert len(fake.stored()) == 1


@pytest.mark.parametrize(
    ("fault", "code"),
    [("down", "ELASTIC_UNREACHABLE"), ("timeout", "ELASTIC_TIMEOUT"), ("401", "ELASTIC_AUTH_FAILED"),
     ("403", "ELASTIC_AUTH_FAILED"), ("429", "ELASTIC_RATE_LIMITED"), ("500", "ELASTIC_UNAVAILABLE")],
)
def test_errors_are_mapped_to_safe_codes(caplog, fault, code):
    caplog.set_level(logging.DEBUG)
    fake = FakeElastic()
    fake.inject(fault)
    client = fake.client()
    with pytest.raises(ElasticError) as exc_info:
        run(client.index_event(event()))
    assert exc_info.value.code == code
    assert exc_info.value.__cause__ is None
    no_secret(str(exc_info.value), repr(exc_info.value), caplog.text, repr(client))


def test_redirects_are_not_followed():
    fake = FakeElastic()
    original = fake._handle
    fake._handle = lambda request: __import__("httpx").Response(302, headers={"Location": "https://attacker.example/"})
    with pytest.raises(ElasticError) as exc_info:
        run(fake.client().index_event(event()))
    assert exc_info.value.code == "ELASTIC_REQUEST_REJECTED"
    assert all(r.url.host == "elastic.test" for r in fake.requests)
    fake._handle = original


def test_search_builds_filtered_sorted_paged_query():
    fake = FakeElastic()
    client = fake.client()

    async def scenario():
        for i in range(5):
            await client.index_event(event(i, transaction_id="txn_a" if i % 2 == 0 else "txn_b",
                                           reviewer_name="Asha" if i == 4 else None))
        return await client.search(q="asha", transaction_id="txn_a", event_type="PAYMENT_CREATED",
                                   limit=2, offset=0)

    items, total = run(scenario())
    body = json.loads(fake.requests[-1].content)
    assert body["sort"][0] == {"occurred_at": {"order": "desc"}}
    assert body["from"] == 0 and body["size"] == 2 and body["track_total_hits"] is True
    assert {"term": {"transaction_id": "txn_a"}} in body["query"]["bool"]["filter"]
    assert {"term": {"event_type": "PAYMENT_CREATED"}} in body["query"]["bool"]["filter"]
    assert total == 1 and items[0].reviewer_name == "Asha"


def test_legacy_comment_in_stored_documents_is_never_returned():
    fake = FakeElastic()
    client = fake.client()
    run(client.index_event(event(1)))
    (doc_id,) = fake.docs[FAKE_INDEX]
    fake.docs[FAKE_INDEX][doc_id]["comment"] = "LEGACY-FREE-TEXT-COMMENT"  # written by schema v1
    items, _ = run(client.search(q=None, transaction_id=None, event_type=None, limit=10, offset=0))
    assert [i.transaction_id for i in items] == ["txn_0001"]
    assert "LEGACY-FREE-TEXT-COMMENT" not in items[0].model_dump_json()
    body = json.loads(fake.requests[-1].content)
    assert body["query"]["bool"]["must"] == [{"match_all": {}}]


def test_comments_are_not_a_search_field():
    from app.services.audit.elastic import SEARCH_FIELDS

    assert "comment" not in SEARCH_FIELDS


def test_search_skips_documents_that_fail_the_allowlist():
    fake = FakeElastic()
    client = fake.client()
    run(client.index_event(event(1)))
    bad = event(2).to_document() | {"event_id": "f" * 32}
    bad["paypal_order_id"] = "not valid"
    fake.docs[FAKE_INDEX]["bad"] = bad
    items, total = run(client.search(q=None, transaction_id=None, event_type=None, limit=10, offset=0))
    assert [i.transaction_id for i in items] == ["txn_0001"]


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"hits": "x"}', b'{"hits": {"hits": [], "total": {"value": -1}}}'])
def test_search_bad_responses(body):
    import httpx

    fake = FakeElastic(existing_mapping=compatible_mapping())
    original = fake._handle

    def handler(request):
        if request.url.path.endswith("/_search"):
            return httpx.Response(200, content=body)
        return original(request)

    fake._handle = handler
    with pytest.raises(ElasticError) as exc_info:
        run(fake.client().search(q=None, transaction_id=None, event_type=None, limit=10, offset=0))
    assert exc_info.value.code == "ELASTIC_BAD_RESPONSE"


# ====================================================================== recorder


def test_not_configured_recorder_keeps_events_in_memory():
    recorder = AuditRecorder(None)
    recorder.emit(event(1))
    result = run(recorder.search())
    assert (result.source, result.durable, result.degraded, result.reason) == (
        "memory_fallback", False, True, "ELASTIC_NOT_CONFIGURED")
    assert "not durably stored" in result.message
    assert [e.transaction_id for e in result.items] == ["txn_0001"]


def test_events_are_flushed_in_order_and_searched_from_elastic():
    fake = FakeElastic()
    recorder = fake.recorder()

    async def scenario():
        for i in range(3):
            recorder.emit(event(i))
        await recorder.drain()
        return await recorder.search()

    result = run(scenario())
    assert (result.source, result.durable, result.degraded, result.reason) == ("elastic", True, False, None)
    assert [e.transaction_id for e in result.items] == ["txn_0002", "txn_0001", "txn_0000"]
    creates = [r.url.path for r in fake.requests if "/_create/" in r.url.path]
    assert len(creates) == 3
    assert recorder.pending_count == 0 and recorder.stored_count == 3


def test_emit_never_raises_or_blocks_without_event_loop():
    fake = FakeElastic()
    recorder = fake.recorder()
    recorder.emit(event())  # no running loop: queued, flushed later
    assert fake.requests == [] and recorder.pending_count == 1
    run(recorder.drain())
    assert recorder.pending_count == 0 and len(fake.stored()) == 1


@pytest.mark.parametrize("fault", ["down", "timeout", "401", "500"])
def test_unavailable_elastic_keeps_events_in_fallback(caplog, fault):
    caplog.set_level(logging.DEBUG)
    fake = FakeElastic()
    fake.inject(*[fault] * 10)
    recorder = fake.recorder()

    async def scenario():
        recorder.emit(event(1))
        recorder.emit(event(2))
        await recorder.drain()
        return await recorder.search(q="acme")

    result = run(scenario())
    assert (result.source, result.durable, result.degraded) == ("memory_fallback", False, True)
    assert result.reason.startswith("ELASTIC_")
    assert result.pending_events == 2 and result.total == 2
    assert "Audit: Elastic unavailable" in caplog.text
    no_secret(caplog.text, result.message)


def test_recovery_flushes_pending_events():
    fake = FakeElastic()
    fake.inject("down")
    recorder = fake.recorder()

    async def scenario():
        recorder.emit(event(1))
        await recorder.drain()
        assert recorder.degraded and recorder.pending_count == 1
        recorder.emit(event(2))  # Elastic is back: both get flushed
        await recorder.drain()
        return await recorder.search()

    result = run(scenario())
    assert result.source == "elastic" and not result.degraded and result.total == 2
    assert recorder.pending_count == 0


def test_lost_response_retry_does_not_duplicate():
    fake = FakeElastic(existing_mapping=compatible_mapping())
    recorder = fake.recorder()

    async def scenario():
        await recorder._client.ensure_index()
        fake.inject("lost")  # Elastic stores the doc, but the response is lost
        recorder.emit(event(1))
        await recorder.drain()
        assert recorder.pending_count == 1  # not confirmed -> still pending
        await recorder.drain()  # retry gets 409 -> confirmed

    run(scenario())
    assert len(fake.stored()) == 1 and recorder.pending_count == 0


def test_rejected_document_does_not_block_later_events():
    fake = FakeElastic(existing_mapping=compatible_mapping())
    recorder = fake.recorder()

    async def scenario():
        await recorder._client.ensure_index()
        fake.inject("reject")
        recorder.emit(event(1))
        recorder.emit(event(2))
        await recorder.drain()
        return await recorder.search()

    result = run(scenario())
    assert [d["transaction_id"] for d in fake.stored()] == ["txn_0002"]
    assert recorder.rejected_count == 1 and recorder.pending_count == 0
    assert result.source == "elastic" and result.degraded is True
    assert "rejected by Elastic" in result.message


def test_fallback_buffer_is_bounded_and_counts_drops():
    recorder = AuditRecorder(None, max_pending=3)
    for i in range(5):
        recorder.emit(event(i))
    result = run(recorder.search())
    assert result.pending_events == 3 and result.dropped_events == 2
    assert [e.transaction_id for e in result.items] == ["txn_0004", "txn_0003", "txn_0002"]
    assert "2 older event(s) were dropped" in result.message


def test_elastic_search_reports_not_yet_stored_events():
    fake = FakeElastic()
    recorder = fake.recorder()

    async def scenario():
        recorder.emit(event(1))
        await recorder.drain()
        fake.inject("down")
        recorder.emit(event(2))
        await recorder.drain()  # event 2 stays pending
        return await recorder.search()

    result = run(scenario())
    assert result.source == "elastic" and result.durable is True and result.degraded is True
    assert result.pending_events == 1
    assert "not yet confirmed in Elastic" in result.message


def test_fallback_search_filters():
    recorder = AuditRecorder(None)
    recorder.emit(event(1, transaction_id="txn_a", event_type=E.REVIEW_APPROVED, reviewer_name="Asha"))
    recorder.emit(event(2, transaction_id="txn_b"))
    recorder.emit(event(3, transaction_id="txn_a", error_code="PAYPAL_TIMEOUT",
                        event_type=E.PAYPAL_OPERATION_FAILED))
    r1 = run(recorder.search(transaction_id="txn_a"))
    r2 = run(recorder.search(event_type="REVIEW_APPROVED"))
    r3 = run(recorder.search(q="paypal_timeout"))
    r4 = run(recorder.search(limit=1, offset=1))
    assert [e.transaction_id for e in r1.items] == ["txn_a", "txn_a"]
    assert [e.reviewer_name for e in r2.items] == ["Asha"]
    assert [e.error_code for e in r3.items] == ["PAYPAL_TIMEOUT"]
    assert r4.total == 3 and len(r4.items) == 1
