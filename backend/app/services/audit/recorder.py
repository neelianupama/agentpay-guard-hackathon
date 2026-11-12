"""Non-blocking audit recorder with an in-memory fallback.

``emit()`` never raises and never waits for Elasticsearch. Each event is put in
an in-memory *pending* buffer and removed only after Elasticsearch confirms it
was stored. A background task flushes pending events in order.

If Elasticsearch is not configured, unreachable, rejects credentials, or has an
incompatible mapping, events stay pending (bounded; oldest dropped past the
limit and counted), a warning is logged, and searches return the pending events
with ``durable: false``. Pending events are NOT durably stored: they are lost
if the backend restarts.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Literal

from app.services.audit.elastic import ElasticAuditClient, ElasticError
from app.services.audit.events import AuditEvent

logger = logging.getLogger(__name__)

DEFAULT_MAX_PENDING = 1000

FALLBACK_MESSAGES = {
    "ELASTIC_NOT_CONFIGURED": "Elastic is not configured. Audit events are held only in backend memory.",
    "ELASTIC_UNREACHABLE": "Elastic is unreachable. Audit events are held only in backend memory.",
    "ELASTIC_TIMEOUT": "Elastic did not respond in time. Audit events are held only in backend memory.",
    "ELASTIC_AUTH_FAILED": "Elastic rejected the configured API key. Audit events are held only in backend memory.",
    "ELASTIC_RATE_LIMITED": "Elastic is rate limiting requests. Audit events are held only in backend memory.",
    "ELASTIC_UNAVAILABLE": "Elastic returned a server error. Audit events are held only in backend memory.",
    "ELASTIC_MAPPING_MISMATCH": (
        "The Elastic audit index has an incompatible mapping. Audit events are held only in backend memory."
    ),
    "ELASTIC_DOCUMENT_REJECTED": "Elastic rejected an audit event. Audit events are held only in backend memory.",
}
DEFAULT_FALLBACK_MESSAGE = "Elastic could not be used. Audit events are held only in backend memory."
NOT_DURABLE_NOTE = " They are not durably stored and are lost if the backend restarts."


@dataclass(frozen=True)
class AuditSearchResult:
    source: Literal["elastic", "memory_fallback"]
    durable: bool
    degraded: bool
    reason: str | None
    message: str
    items: list[AuditEvent]
    total: int
    pending_events: int
    dropped_events: int


class AuditRecorder:
    def __init__(self, client: ElasticAuditClient | None, *, max_pending: int = DEFAULT_MAX_PENDING) -> None:
        self._client = client
        self._pending: deque[AuditEvent] = deque()
        # Events Elastic permanently refused (HTTP 400): kept in memory, never retried.
        self._rejected: deque[AuditEvent] = deque(maxlen=max_pending)
        self._max_pending = max_pending
        self._dropped = 0
        self._stored = 0
        self._flush_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None if client else "ELASTIC_NOT_CONFIGURED"

    # ------------------------------------------------------------------ state

    @property
    def configured(self) -> bool:
        return self._client is not None

    @property
    def degraded(self) -> bool:
        return self._last_error is not None

    @property
    def reason(self) -> str | None:
        return self._last_error

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    @property
    def stored_count(self) -> int:
        return self._stored

    @property
    def rejected_count(self) -> int:
        return len(self._rejected)

    def pending_events(self) -> list[AuditEvent]:
        return list(self._pending)

    # ------------------------------------------------------------------ write

    def emit(self, event: AuditEvent) -> None:
        """Queue an event for Elastic. Never raises; never blocks the caller."""
        try:
            if len(self._pending) >= self._max_pending:
                self._pending.popleft()
                self._dropped += 1
                logger.warning("Audit: fallback buffer full; dropped the oldest pending event")
            self._pending.append(event)
            self._schedule_flush()
        except Exception:  # noqa: BLE001 - auditing must never break payments
            logger.exception("Audit: failed to queue an event")

    def _schedule_flush(self) -> None:
        if self._client is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop here; the next emit/search/drain will flush
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = loop.create_task(self._flush())

    async def _flush(self) -> None:
        assert self._client is not None
        while self._pending:
            event = self._pending[0]
            try:
                await self._client.index_event(event)
            except ElasticError as exc:
                if exc.code == "ELASTIC_DOCUMENT_REJECTED":
                    # Retrying cannot succeed; don't block later events behind it.
                    if self._pending and self._pending[0] is event:
                        self._rejected.append(self._pending.popleft())
                    self._mark_degraded(exc.code)
                    continue
                self._mark_degraded(exc.code)
                return
            except Exception:  # noqa: BLE001
                self._mark_degraded("ELASTIC_ERROR")
                return
            if self._pending and self._pending[0] is event:
                self._pending.popleft()
            self._stored += 1
            if self._last_error is not None and self._last_error != "ELASTIC_DOCUMENT_REJECTED":
                logger.info("Audit: Elastic available again; flushing pending events")
                self._last_error = None

    def _mark_degraded(self, code: str) -> None:
        if self._last_error != code:
            logger.warning("Audit: Elastic unavailable (%s); %d event(s) kept in memory only",
                           code, len(self._pending))
        self._last_error = code

    async def drain(self) -> None:
        """Flush pending events now (used at shutdown and in tests). Never raises."""
        if self._client is None:
            return
        task = self._flush_task
        if task is not None and not task.done():
            # Wait for the scheduled flush attempt; do not immediately retry after it fails.
            try:
                await task
            except Exception:  # noqa: BLE001
                pass
        elif self._pending:
            await self._flush()  # nothing scheduled (e.g. emitted without an event loop)

    async def aclose(self) -> None:
        await self.drain()
        if self._client is not None:
            await self._client.aclose()

    # ----------------------------------------------------------------- search

    async def search(
        self,
        *,
        q: str | None = None,
        transaction_id: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditSearchResult:
        if self._client is not None:
            try:
                items, total = await self._client.search(
                    q=q, transaction_id=transaction_id, event_type=event_type,
                    limit=limit, offset=offset,
                )
            except ElasticError as exc:
                self._mark_degraded(exc.code)
            except Exception:  # noqa: BLE001
                self._mark_degraded("ELASTIC_ERROR")
            else:
                if self._pending:
                    self._schedule_flush()  # Elastic is reachable: retry pending events
                message = "Results from Elastic."
                if self._rejected:
                    message += f" {len(self._rejected)} event(s) were rejected by Elastic and are held only in memory."
                if self._pending:
                    message += (
                        f" {len(self._pending)} newer event(s) are not yet confirmed in Elastic "
                        "and are not included."
                    )
                not_stored = bool(self._pending or self._rejected)
                return AuditSearchResult(
                    source="elastic", durable=True, degraded=not_stored,
                    reason=self._last_error if not_stored else None, message=message,
                    items=items, total=total,
                    pending_events=len(self._pending), dropped_events=self._dropped,
                )
        return self._search_fallback(q, transaction_id, event_type, limit, offset)

    def _search_fallback(self, q, transaction_id, event_type, limit, offset) -> AuditSearchResult:
        events = list(self._rejected) + list(self._pending)
        if transaction_id:
            events = [e for e in events if e.transaction_id == transaction_id]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if q:
            terms = q.casefold().split()
            events = [e for e in events if all(t in _searchable_text(e) for t in terms)]
        events.sort(key=lambda e: (e.occurred_at, e.sequence), reverse=True)
        reason = self._last_error or "ELASTIC_NOT_CONFIGURED"
        message = FALLBACK_MESSAGES.get(reason, DEFAULT_FALLBACK_MESSAGE) + NOT_DURABLE_NOTE
        if self._dropped:
            message += f" {self._dropped} older event(s) were dropped because the buffer was full."
        return AuditSearchResult(
            source="memory_fallback", durable=False, degraded=True, reason=reason, message=message,
            items=events[offset : offset + limit], total=len(events),
            pending_events=len(self._pending), dropped_events=self._dropped,
        )


def _searchable_text(event: AuditEvent) -> str:
    parts = [
        event.merchant_name, event.reviewer_name, event.transaction_id,
        str(event.event_type), event.status and str(event.status), event.decision and str(event.decision),
        event.error_code, event.outcome, *event.reasons,
    ]
    return " ".join(p for p in parts if p).casefold()
