"""Audit trail: allowlisted events indexed in Elasticsearch, with an in-memory fallback."""

from app.services.audit.elastic import INDEX_MAPPING, ElasticAuditClient, ElasticError
from app.services.audit.events import (
    ALLOWED_FIELDS,
    AuditEvent,
    AuditEventType,
    AuditOperation,
    payment_event,
    risk_assessed,
)
from app.services.audit.recorder import AuditRecorder, AuditSearchResult

__all__ = [
    "ALLOWED_FIELDS",
    "INDEX_MAPPING",
    "AuditEvent",
    "AuditEventType",
    "AuditOperation",
    "AuditRecorder",
    "AuditSearchResult",
    "ElasticAuditClient",
    "ElasticError",
    "payment_event",
    "risk_assessed",
]
