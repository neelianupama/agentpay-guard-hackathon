"""Audit search API contract (``GET /api/v1/audit/events``)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import APIModel
from app.services.audit.events import AuditEvent


class AuditSearchResponse(APIModel):
    source: Literal["elastic", "memory_fallback"] = Field(
        description="Where the results came from. 'memory_fallback' means Elastic could not be used."
    )
    durable: bool = Field(
        description="True only for results read from Elastic. Fallback events are NOT durably stored."
    )
    degraded: bool = Field(
        description="True if Elastic is not configured/unavailable or some events are not yet stored in Elastic."
    )
    reason: str | None = Field(default=None, description="Fixed code explaining the degraded state.")
    message: str
    pending_events: int = Field(ge=0, description="Events held in memory, not yet confirmed in Elastic.")
    dropped_events: int = Field(ge=0, description="Events dropped because the memory buffer was full.")
    items: list[AuditEvent]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
