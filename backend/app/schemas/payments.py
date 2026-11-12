"""Payment workflow API contracts (``/api/v1/payments``).

Request bodies never carry a risk score, risk level, decision, reasons,
status, PayPal status or buyer-approval flag: unknown fields are rejected
(``extra="forbid"``). The response never contains PayPal credentials, access
tokens, the reviewer token or idempotency request IDs.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from app.schemas.common import (
    APIModel,
    CurrencyCode,
    LongText,
    MoneyAmount,
    ShortText,
    TransactionId,
    UtcDatetime,
)
from app.schemas.enums import ApprovalDecision, PaymentStatus, PolicyDecision, RiskLevel

ReviewerComment = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
ReviewerName = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class ReviewerActionRequest(APIModel):
    """Optional context for a reviewer action. Authentication is via the Authorization header only."""

    reviewer_name: ReviewerName | None = Field(
        default=None, description="Who is acting (self-declared label for the audit trail)."
    )
    comment: ReviewerComment | None = Field(default=None, description="Reason for the action.")


class PaymentRiskSummary(APIModel):
    """Risk assessment computed by the backend risk engine at creation time."""

    risk_score: int = Field(ge=0, le=100)
    risk_level: RiskLevel
    decision: PolicyDecision
    reasons: list[str]
    assessed_at: UtcDatetime


class PayPalSummary(APIModel):
    """PayPal references (Sandbox). Statuses are as last reported by PayPal."""

    order_id: str | None = None
    approval_url: str | None = Field(
        default=None, description="Where the buyer approves the order on PayPal Sandbox."
    )
    order_status: str | None = None
    authorization_id: str | None = None
    authorization_status: str | None = None
    capture_id: str | None = None
    capture_status: str | None = None


class ReviewSummary(APIModel):
    """Reviewer decision metadata. The reviewer token is never stored."""

    decision: ApprovalDecision
    reviewer_name: str | None = None
    comment: str | None = None
    decided_at: UtcDatetime


class ReviewerActionLog(APIModel):
    action: str
    reviewer_name: str | None = None
    comment: str | None = None
    at: UtcDatetime


class PaymentErrorSummary(APIModel):
    """Most recent operation problem (fixed, client-safe text)."""

    code: str
    message: str
    retry_safe: bool = Field(
        description="True if repeating the same action is safe (it reuses the stored request ID)."
    )
    at: UtcDatetime


class PaymentRecordResponse(APIModel):
    transaction_id: TransactionId
    status: PaymentStatus
    merchant_name: ShortText
    amount: MoneyAmount
    currency: CurrencyCode
    description: LongText
    purpose: ShortText
    risk: PaymentRiskSummary
    paypal: PayPalSummary
    review: ReviewSummary | None = None
    reviewer_actions: list[ReviewerActionLog] = Field(default_factory=list)
    last_error: PaymentErrorSummary | None = None
    next_actions: list[str] = Field(
        description="Actions currently allowed for this payment (informational)."
    )
    created_at: UtcDatetime
    updated_at: UtcDatetime


class PaymentListResponse(APIModel):
    items: list[PaymentRecordResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


# --------------------------------------------------------------------------------------
# Minimal responses for the UNAUTHENTICATED buyer/agent actions
# (POST /payments, POST /{id}/order, POST /{id}/authorize).
#
# Explicit allowlist: only what the buyer flow needs. Never includes reviewer names,
# comments or actions, risk scores/levels/reasons, description/purpose, PayPal order/
# authorization/capture IDs or statuses, error messages or timestamps. The complete
# record is available only through the reviewer-authenticated read endpoints.
# --------------------------------------------------------------------------------------


class PaymentActionError(APIModel):
    """Last operation problem: fixed code only (clients map codes to their own text)."""

    code: str
    retry_safe: bool


class PaymentActionResponse(APIModel):
    transaction_id: TransactionId
    status: PaymentStatus
    merchant_name: ShortText
    amount: MoneyAmount
    currency: CurrencyCode
    approval_url: str | None = Field(
        default=None,
        description="PayPal Sandbox page where the buyer approves the order "
        "(only while status is AWAITING_BUYER_APPROVAL).",
    )
    next_actions: list[str] = Field(
        description="Buyer/agent actions currently allowed (create_order, "
        "buyer_approves_on_paypal, authorize). Reviewer actions are not listed."
    )
    last_error: PaymentActionError | None = None
