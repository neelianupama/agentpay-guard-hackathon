"""Minimal Elasticsearch REST client for audit events (httpx, no extra packages).

Security: the API key is sent only as ``Authorization: ApiKey ...``; it never
appears in logs, exceptions or ``repr``. Redirects are not followed. Errors are
mapped to fixed codes; Elasticsearch response bodies are never surfaced.

Index safety: the index is created with ``"dynamic": "strict"`` so Elasticsearch
itself rejects any non-allowlisted field. An existing index is only verified,
never modified; an incompatible mapping is reported, not overwritten.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.services.audit.events import AuditEvent

logger = logging.getLogger(__name__)

_KEYWORD = {"type": "keyword"}
INDEX_MAPPING: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "schema_version": {"type": "integer"},
        "event_id": _KEYWORD,
        "event_type": _KEYWORD,
        "occurred_at": {"type": "date"},
        "sequence": {"type": "long"},
        "transaction_id": _KEYWORD,
        "status": _KEYWORD,
        "previous_status": _KEYWORD,
        "operation": _KEYWORD,
        "outcome": _KEYWORD,
        "decision": _KEYWORD,
        "risk_level": _KEYWORD,
        "risk_score": {"type": "integer"},
        "reasons": {"type": "text"},
        "merchant_name": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
        "amount": _KEYWORD,
        "currency": _KEYWORD,
        "reviewer_name": _KEYWORD,
        # Legacy field from the first schema; no longer written or returned. Kept so an
        # index created by that version still verifies (the mapping is never modified).
        "comment": {"type": "text"},
        "paypal_order_id": _KEYWORD,
        "paypal_authorization_id": _KEYWORD,
        "paypal_capture_id": _KEYWORD,
        "paypal_status": _KEYWORD,
        "error_code": _KEYWORD,
        "retry_safe": {"type": "boolean"},
    },
}

# Mapped for backwards compatibility only; stripped from any document read back.
LEGACY_FIELDS = frozenset({"comment"})

SEARCH_FIELDS = [
    "merchant_name", "reasons", "reviewer_name", "transaction_id",
    "event_type", "status", "decision", "error_code", "outcome",
]


class ElasticError(Exception):
    """Fixed, client-safe code describing why Elastic could not be used."""

    def __init__(self, code: str, *, upstream_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.upstream_status = upstream_status

    def __repr__(self) -> str:
        return f"ElasticError(code={self.code!r}, upstream_status={self.upstream_status!r})"


class ElasticAuditClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        index: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._index = index
        self._index_path = "/" + quote(index, safe="")
        self._headers = {
            "Authorization": f"ApiKey {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self._timeout = httpx.Timeout(timeout_seconds)
        self._owns_client = http_client is None
        self._http = http_client or httpx.AsyncClient(follow_redirects=False)
        self._index_ready = False

    def __repr__(self) -> str:
        return f"ElasticAuditClient(index={self._index!r}, index_ready={self._index_ready})"

    @property
    def index(self) -> str:
        return self._index

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _send(self, method: str, path: str, *, json: Any = None, params: dict | None = None) -> httpx.Response:
        try:
            return await self._http.request(
                method, f"{self._base_url}{path}", json=json, params=params,
                headers=self._headers, timeout=self._timeout, follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ElasticError("ELASTIC_TIMEOUT") from None
        except httpx.TransportError:
            raise ElasticError("ELASTIC_UNREACHABLE") from None

    @staticmethod
    def _check(response: httpx.Response, *, ok: tuple[int, ...] = (200, 201)) -> None:
        status = response.status_code
        if status in ok:
            return
        if status in (401, 403):
            raise ElasticError("ELASTIC_AUTH_FAILED", upstream_status=status)
        if status == 429:
            raise ElasticError("ELASTIC_RATE_LIMITED", upstream_status=status)
        if status >= 500:
            raise ElasticError("ELASTIC_UNAVAILABLE", upstream_status=status)
        raise ElasticError("ELASTIC_REQUEST_REJECTED", upstream_status=status)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            raise ElasticError("ELASTIC_BAD_RESPONSE", upstream_status=response.status_code) from None
        if not isinstance(body, dict):
            raise ElasticError("ELASTIC_BAD_RESPONSE", upstream_status=response.status_code)
        return body

    # ------------------------------------------------------------------ index

    async def ensure_index(self) -> None:
        """Create the index with the strict mapping, or verify an existing one."""
        if self._index_ready:
            return
        response = await self._send("HEAD", self._index_path)
        if response.status_code == 404:
            created = await self._send("PUT", self._index_path, json={"mappings": INDEX_MAPPING})
            if created.status_code == 400 and _already_exists(created):
                pass  # created concurrently; verify below
            else:
                self._check(created, ok=(200,))
                logger.info("Audit: created Elastic index %s", self._index)
                self._index_ready = True
                return
        else:
            self._check(response, ok=(200,))
        await self._verify_mapping()
        self._index_ready = True

    async def _verify_mapping(self) -> None:
        body = self._json(await self._checked("GET", f"{self._index_path}/_mapping"))
        indices = [v for v in body.values() if isinstance(v, dict)]
        if not indices:
            raise ElasticError("ELASTIC_BAD_RESPONSE")
        for index_body in indices:  # an alias may point at several indices
            properties = (index_body.get("mappings") or {}).get("properties") or {}
            for name, spec in INDEX_MAPPING["properties"].items():
                if (properties.get(name) or {}).get("type") != spec["type"]:
                    logger.warning("Audit: Elastic index mapping is incompatible (field %s)", name)
                    raise ElasticError("ELASTIC_MAPPING_MISMATCH")

    async def _checked(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self._send(method, path, **kwargs)
        self._check(response, ok=(200,))
        return response

    # ------------------------------------------------------------------ write

    async def index_event(self, event: AuditEvent) -> None:
        """Store one event idempotently (``_create`` with the event ID)."""
        await self.ensure_index()
        response = await self._send(
            "PUT", f"{self._index_path}/_create/{event.event_id}", json=event.to_document()
        )
        if response.status_code == 409:
            return  # already stored by an earlier attempt whose response was lost
        if response.status_code == 400:
            # e.g. strict-mapping rejection: retrying the same document cannot succeed
            raise ElasticError("ELASTIC_DOCUMENT_REJECTED", upstream_status=400)
        self._check(response)

    # ----------------------------------------------------------------- search

    async def search(
        self,
        *,
        q: str | None,
        transaction_id: str | None,
        event_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditEvent], int]:
        await self.ensure_index()
        filters: list[dict[str, Any]] = []
        if transaction_id:
            filters.append({"term": {"transaction_id": transaction_id}})
        if event_type:
            filters.append({"term": {"event_type": event_type}})
        must: list[dict[str, Any]] = []
        if q:
            must.append({"simple_query_string": {
                "query": q, "fields": SEARCH_FIELDS, "default_operator": "and", "lenient": True,
            }})
        body = {
            "from": offset,
            "size": limit,
            "track_total_hits": True,
            "sort": [{"occurred_at": {"order": "desc"}}, {"sequence": {"order": "desc"}}],
            "query": {"bool": {"filter": filters, "must": must or [{"match_all": {}}]}},
        }
        data = self._json(await self._checked("POST", f"{self._index_path}/_search", json=body))
        hits = data.get("hits")
        if not isinstance(hits, dict) or not isinstance(hits.get("hits"), list):
            raise ElasticError("ELASTIC_BAD_RESPONSE")
        total = hits.get("total", {})
        total_value = total.get("value") if isinstance(total, dict) else total
        if not isinstance(total_value, int) or total_value < 0:
            raise ElasticError("ELASTIC_BAD_RESPONSE")
        events: list[AuditEvent] = []
        for hit in hits["hits"]:
            source = hit.get("_source") if isinstance(hit, dict) else None
            if isinstance(source, dict):
                source = {k: v for k, v in source.items() if k not in LEGACY_FIELDS}
            try:
                events.append(AuditEvent.model_validate(source))
            except Exception:  # noqa: BLE001 - skip documents that fail the allowlist
                logger.warning("Audit: skipped an Elastic document that failed validation")
        return events, total_value


def _already_exists(response: httpx.Response) -> bool:
    try:
        error = response.json().get("error", {})
    except (ValueError, AttributeError):
        return False
    return isinstance(error, dict) and error.get("type") == "resource_already_exists_exception"
