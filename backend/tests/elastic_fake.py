"""In-memory stand-in for the Elasticsearch REST API (tests only; httpx.MockTransport).

Models: index create/verify (HEAD, PUT, GET _mapping), strict-mapping document
rejection, idempotent ``_create`` (409 on duplicate), and ``_search`` with term
filters, a simple query string, sort and pagination.

Faults (``inject(kind, ...)``, consumed FIFO across requests):
``down`` (connection refused), ``timeout``, ``401``, ``403``, ``429``, ``500``,
``lost`` (document stored, response lost -> timeout), ``reject`` (400).
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.services.audit import INDEX_MAPPING, AuditRecorder, ElasticAuditClient
from app.services.audit.elastic import SEARCH_FIELDS

FAKE_ELASTIC_URL = "https://elastic.test:9243"
FAKE_ELASTIC_KEY = "FAKE-elastic-api-key-c2VjcmV0"
FAKE_INDEX = "agentpay-audit-test"


class FakeElastic:
    def __init__(self, *, existing_mapping: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.indices: dict[str, dict] = {}
        self.docs: dict[str, dict[str, dict]] = {}
        self.faults: list[str] = []
        if existing_mapping is not None:
            self.indices[FAKE_INDEX] = existing_mapping
            self.docs[FAKE_INDEX] = {}

    def inject(self, *kinds: str) -> None:
        self.faults.extend(kinds)

    def stored(self, index: str = FAKE_INDEX) -> list[dict]:
        return list(self.docs.get(index, {}).values())

    def client(self) -> ElasticAuditClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return ElasticAuditClient(base_url=FAKE_ELASTIC_URL, api_key=FAKE_ELASTIC_KEY,
                                  index=FAKE_INDEX, timeout_seconds=5, http_client=http)

    def recorder(self, **kwargs: Any) -> AuditRecorder:
        return AuditRecorder(self.client(), **kwargs)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        fault = self.faults.pop(0) if self.faults else None
        if fault == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if fault == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if fault in ("401", "403", "429", "500"):
            return httpx.Response(int(fault), json={"error": {"reason": "FAKE-raw-elastic-error"}})
        if fault == "reject":
            return httpx.Response(400, json={"error": {"type": "mapper_parsing_exception"}})
        response = self._handle(request)
        if fault == "lost":
            raise httpx.ReadTimeout("response lost", request=request)
        return response

    def _handle(self, request: httpx.Request) -> httpx.Response:
        parts = [p for p in request.url.path.split("/") if p]
        index = parts[0] if parts else ""
        method = request.method
        if len(parts) == 1 and method == "HEAD":
            return httpx.Response(200 if index in self.indices else 404)
        if len(parts) == 1 and method == "PUT":
            if index in self.indices:
                return httpx.Response(400, json={"error": {"type": "resource_already_exists_exception"}})
            self.indices[index] = json.loads(request.content)["mappings"]
            self.docs[index] = {}
            return httpx.Response(200, json={"acknowledged": True, "index": index})
        if index not in self.indices:
            return httpx.Response(404, json={"error": {"type": "index_not_found_exception"}})
        if parts[1:] == ["_mapping"] and method == "GET":
            return httpx.Response(200, json={index: {"mappings": self.indices[index]}})
        if len(parts) == 3 and parts[1] == "_create" and method == "PUT":
            doc_id, doc = parts[2], json.loads(request.content)
            mapping = self.indices[index]
            if mapping.get("dynamic") == "strict" and set(doc) - set(mapping["properties"]):
                return httpx.Response(400, json={"error": {"type": "strict_dynamic_mapping_exception"}})
            if doc_id in self.docs[index]:
                return httpx.Response(409, json={"error": {"type": "version_conflict_engine_exception"}})
            self.docs[index][doc_id] = doc
            return httpx.Response(201, json={"_id": doc_id, "result": "created"})
        if parts[1:] == ["_search"] and method == "POST":
            return httpx.Response(200, json=self._search(index, json.loads(request.content)))
        return httpx.Response(400, json={"error": {"type": "unsupported"}})

    def _search(self, index: str, body: dict) -> dict:
        docs = list(self.docs[index].values())
        query = body["query"]["bool"]
        for f in query["filter"]:
            ((field, value),) = f["term"].items()
            docs = [d for d in docs if d.get(field) == value]
        for m in query["must"]:
            if "simple_query_string" in m:
                terms = m["simple_query_string"]["query"].casefold().split()
                docs = [d for d in docs if all(t in _text(d) for t in terms)]
        docs.sort(key=lambda d: (d["occurred_at"], d["sequence"]), reverse=True)
        page = docs[body["from"] : body["from"] + body["size"]]
        return {"hits": {"total": {"value": len(docs), "relation": "eq"},
                         "hits": [{"_id": d["event_id"], "_source": d} for d in page]}}


def _text(doc: dict) -> str:
    values: list[Any] = []
    for field in SEARCH_FIELDS:
        v = doc.get(field)
        values.extend(v if isinstance(v, list) else [v])
    return " ".join(str(v) for v in values if v is not None).casefold()


def compatible_mapping() -> dict:
    return json.loads(json.dumps(INDEX_MAPPING))
