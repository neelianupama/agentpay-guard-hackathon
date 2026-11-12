"""Audit event model with an explicit field allowlist.

``AuditEvent`` forbids unknown fields and constrains every value. Builders in
this module copy only allowlisted, non-secret data. Never included: intent
request text, PayPal credentials or access tokens, the reviewer token, PayPal
request IDs (idempotency keys), approval URLs, authorization headers or raw
PayPal responses. Payment description and purpose, and reviewer comments,
are also left out to minimise free-text personal data in the audit index.
"""

from __future__ import annotations

import itertools
import uuid
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from app.schemas.common import APIModel, UtcDatetime, utc_now
from app.schemas.enums import PaymentStatus, PolicyDecision, RiskLevel
from app.schemas.payment import TransactionDetails
from app.schemas.risk import RiskAssessment

SCHEMA_VERSION = 1

# Monotonic per-process sequence: orders events that share a timestamp.
_SEQUENCE = itertools.count(1)


class AuditEventType(StrEnum):
    RISK_ASSESSED = "RISK_ASSESSED"
    PAYMENT_CREATED = "PAYMENT_CREATED"
    PAYMENT_STATUS_CHANGED = "PAYMENT_STATUS_CHANGED"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    PAYPAL_ORDER_CREATED = "PAYPAL_ORDER_CREATED"
    PAYPAL_AUTHORIZATION = "PAYPAL_AUTHORIZATION"
    PAYPAL_CAPTURE = "PAYPAL_CAPTURE"
    PAYPAL_VOID = "PAYPAL_VOID"
    PAYPAL_OPERATION_FAILED = "PAYPAL_OPERATION_FAILED"


class AuditOperation(StrEnum):
    CREATE_ORDER = "CREATE_ORDER"
    CHECK_BUYER_APPROVAL = "CHECK_BUYER_APPROVAL"
    AUTHORIZE = "AUTHORIZE"
    REFRESH_AUTHORIZATION = "REFRESH_AUTHORIZATION"
    CAPTURE = "CAPTURE"
    VOID = "VOID"


Code = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
PayPalId = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{10,36}$")]
TxnId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Amount = Annotated[str, StringConstraints(pattern=r"^\d{1,12}(\.\d{1,2})?$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Reason = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class AuditEvent(APIModel):
    """One audit fact. ``extra="forbid"``: any field outside this list is rejected."""

    schema_version: int = SCHEMA_VERSION
    event_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")] = Field(
        default_factory=lambda: uuid.uuid4().hex
    )
    event_type: AuditEventType
    occurred_at: UtcDatetime = Field(default_factory=utc_now)
    sequence: int = Field(default_factory=lambda: next(_SEQUENCE), ge=0)

    transaction_id: TxnId | None = None
    status: PaymentStatus | None = None
    previous_status: PaymentStatus | None = None
    operation: AuditOperation | None = None
    outcome: Code | None = None

    decision: PolicyDecision | None = None
    risk_level: RiskLevel | None = None
    risk_score: int | None = Field(default=None, ge=0, le=100)
    reasons: list[Reason] = Field(default_factory=list, max_length=25)

    merchant_name: Annotated[str, StringConstraints(max_length=200)] | None = None
    amount: Amount | None = None
    currency: Currency | None = None

    # Reviewer comments are free text and are deliberately NOT part of audit events.
    reviewer_name: Annotated[str, StringConstraints(max_length=100)] | None = None

    paypal_order_id: PayPalId | None = None
    paypal_authorization_id: PayPalId | None = None
    paypal_capture_id: PayPalId | None = None
    paypal_status: Code | None = None

    error_code: Code | None = None
    retry_safe: bool | None = None

    def to_document(self) -> dict[str, Any]:
        """JSON document for Elasticsearch (only allowlisted fields, nulls dropped)."""
        return self.model_dump(mode="json", exclude_none=True)


ALLOWED_FIELDS = frozenset(AuditEvent.model_fields)


def _amount(value: Any) -> str:
    return f"{value:.2f}" if not isinstance(value, str) else value


def risk_assessed(details: TransactionDetails, assessment: RiskAssessment) -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.RISK_ASSESSED,
        decision=assessment.decision,
        risk_level=assessment.risk_level,
        risk_score=assessment.risk_score,
        reasons=list(assessment.reasons),
        merchant_name=details.merchant_name,
        amount=_amount(details.amount),
        currency=details.currency,
    )


def payment_event(record: Any, event_type: AuditEventType, **fields: Any) -> AuditEvent:
    """Event about a payment record. Only allowlisted record fields are copied."""
    base: dict[str, Any] = {
        "event_type": event_type,
        "transaction_id": record.transaction_id,
        "status": record.status,
        "decision": record.decision,
        "risk_level": record.risk_level,
        "risk_score": record.risk_score,
        "merchant_name": record.merchant_name,
        "amount": _amount(record.amount),
        "currency": record.currency,
    }
    base.update(fields)
    return AuditEvent(**base)
