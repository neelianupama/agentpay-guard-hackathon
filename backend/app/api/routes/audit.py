"""Audit trail API: search allowlisted audit events (Elastic, or in-memory fallback)."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.security import ReviewerRequired
from app.core.config import Settings, get_settings
from app.schemas.audit import AuditSearchResponse
from app.schemas.error import ErrorResponse
from app.services.audit import AuditEventType, AuditRecorder, ElasticAuditClient

router = APIRouter(prefix="/audit", tags=["audit"])

MAX_RESULT_WINDOW = 10_000  # Elasticsearch default index.max_result_window


@lru_cache(maxsize=4)
def _recorder_for(settings: Settings) -> AuditRecorder:
    if not settings.elastic_configured:
        return AuditRecorder(None)
    assert settings.elastic_url and settings.elastic_api_key
    client = ElasticAuditClient(
        base_url=settings.elastic_url,
        api_key=settings.elastic_api_key,
        index=settings.elastic_index,
        timeout_seconds=settings.elastic_timeout_seconds,
    )
    return AuditRecorder(client)


def get_audit_recorder(settings: Annotated[Settings, Depends(get_settings)]) -> AuditRecorder:
    """Shared recorder (one per settings). Tests override this dependency."""
    return _recorder_for(settings)


Recorder = Annotated[AuditRecorder, Depends(get_audit_recorder)]


@router.get(
    "/events",
    response_model=AuditSearchResponse,
    operation_id="search_audit_events",
    summary="Search audit events",
    description=(
        "Searches allowlisted audit events: risk decisions, payment creation and status changes, "
        "reviewer approvals/rejections and PayPal order, authorization, capture and void outcomes "
        "(with safe error codes).\n\n"
        "`source` is `elastic` when results come from Elasticsearch (`durable: true`). If Elastic is "
        "not configured or unavailable, `source` is `memory_fallback`, `durable` is false and "
        "`degraded` is true: those events are held only in backend memory and are lost on restart.\n\n"
        "Never contains intent request text, reviewer comments, tokens, credentials, PayPal "
        "request IDs or raw PayPal responses.\n\n"
        "**Requires reviewer authentication:** `Authorization: Bearer <REVIEWER_TOKEN>` "
        "(never in the query string). Missing/invalid token: 401; reviewer authentication not "
        "configured on the server: 503."
    ),
    responses={
        401: {"model": ErrorResponse, "description": "Reviewer authentication missing or invalid."},
        503: {"model": ErrorResponse, "description": "Reviewer authentication not configured."},
    },
)
async def search_audit_events(
    _: ReviewerRequired,
    recorder: Recorder,
    q: Annotated[str | None, Query(max_length=200, description="Free-text search.")] = None,
    transaction_id: Annotated[
        str | None, Query(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    ] = None,
    event_type: AuditEventType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_RESULT_WINDOW - 1)] = 0,
) -> AuditSearchResponse:
    limit = min(limit, MAX_RESULT_WINDOW - offset)
    result = await recorder.search(
        q=(q or "").strip() or None,
        transaction_id=transaction_id,
        event_type=event_type.value if event_type else None,
        limit=limit,
        offset=offset,
    )
    return AuditSearchResponse(
        source=result.source,
        durable=result.durable,
        degraded=result.degraded,
        reason=result.reason,
        message=result.message,
        pending_events=result.pending_events,
        dropped_events=result.dropped_events,
        items=result.items,
        total=result.total,
        limit=limit,
        offset=offset,
    )
