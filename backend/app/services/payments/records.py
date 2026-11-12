"""Payment record model and the workflow state machine.

Every status change goes through :func:`transition`, which only permits the
edges in :data:`ALLOWED_TRANSITIONS`. API clients cannot set a status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.schemas.enums import ApprovalDecision, PaymentStatus, PolicyDecision, RiskLevel
from app.services.payments.errors import InvalidPaymentTransitionError

S = PaymentStatus

ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    S.BLOCKED: frozenset(),
    S.PENDING_REVIEW: frozenset({S.APPROVED_FOR_PAYMENT, S.REJECTED}),
    S.REJECTED: frozenset(),
    S.APPROVED_FOR_PAYMENT: frozenset({S.AWAITING_BUYER_APPROVAL, S.FAILED}),
    S.AWAITING_BUYER_APPROVAL: frozenset({S.BUYER_APPROVED, S.FAILED}),
    S.BUYER_APPROVED: frozenset({S.AUTHORIZED, S.AUTHORIZATION_PENDING, S.FAILED}),
    S.AUTHORIZATION_PENDING: frozenset({S.AUTHORIZED, S.FAILED}),
    S.AUTHORIZED: frozenset({S.PENDING_CAPTURE, S.PENDING_VOID}),
    S.PENDING_CAPTURE: frozenset({S.CAPTURED, S.CAPTURE_PENDING, S.FAILED}),
    S.CAPTURE_PENDING: frozenset(),
    S.CAPTURED: frozenset(),
    S.PENDING_VOID: frozenset({S.VOIDED, S.FAILED}),
    S.VOIDED: frozenset(),
    S.FAILED: frozenset(),
}

TERMINAL_STATUSES = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)

NEXT_ACTIONS: dict[PaymentStatus, tuple[str, ...]] = {
    S.PENDING_REVIEW: ("approve", "reject"),
    S.APPROVED_FOR_PAYMENT: ("create_order",),
    S.AWAITING_BUYER_APPROVAL: ("buyer_approves_on_paypal", "authorize"),
    S.BUYER_APPROVED: ("authorize",),
    S.AUTHORIZATION_PENDING: ("authorize",),
    S.AUTHORIZED: ("capture", "void"),
    S.PENDING_CAPTURE: ("capture",),
    S.PENDING_VOID: ("void",),
}


@dataclass(frozen=True)
class ReviewDecision:
    decision: ApprovalDecision
    reviewer_name: str | None
    comment: str | None
    decided_at: datetime


@dataclass(frozen=True)
class ReviewerAction:
    action: str  # approve | reject | capture | void
    reviewer_name: str | None
    comment: str | None
    at: datetime


@dataclass(frozen=True)
class LastError:
    code: str
    message: str
    retry_safe: bool
    at: datetime


@dataclass
class PaymentRecord:
    """One payment. Money is Decimal; nothing secret is ever stored here."""

    transaction_id: str
    merchant_name: str
    description: str
    purpose: str
    amount: Decimal
    currency: str

    # Backend risk assessment (authoritative; never client-supplied).
    risk_score: int
    risk_level: RiskLevel
    decision: PolicyDecision
    reasons: tuple[str, ...]
    assessed_at: datetime

    status: PaymentStatus
    created_at: datetime
    updated_at: datetime

    # PayPal references (validated by PayPalOrdersService before storage).
    paypal_order_id: str | None = None
    paypal_approval_url: str | None = None
    paypal_order_status: str | None = None
    paypal_authorization_id: str | None = None
    paypal_authorization_status: str | None = None
    paypal_capture_id: str | None = None
    paypal_capture_status: str | None = None

    # Stable PayPal-Request-Id per operation; generated once, reused on retries.
    create_request_id: str | None = None
    authorize_request_id: str | None = None
    capture_request_id: str | None = None
    void_request_id: str | None = None

    review: ReviewDecision | None = None
    reviewer_actions: list[ReviewerAction] = field(default_factory=list)
    last_error: LastError | None = None


def transition(record: PaymentRecord, new_status: PaymentStatus, *, at: datetime) -> None:
    """Move ``record`` to ``new_status`` or raise if the edge is not allowed."""
    if new_status not in ALLOWED_TRANSITIONS[record.status]:
        raise InvalidPaymentTransitionError(
            f"Cannot change payment status from {record.status} to {new_status}.",
            status=str(record.status),
        )
    record.status = new_status
    record.updated_at = at


def require_status(record: PaymentRecord, *allowed: PaymentStatus, action: str) -> None:
    if record.status not in allowed:
        raise InvalidPaymentTransitionError(
            f"Cannot {action} a payment with status {record.status}.",
            transaction_id=record.transaction_id,
            status=str(record.status),
        )
