"""Enumerations for fixed statuses and decisions.

All enums are ``StrEnum`` so they serialize as their plain string values
in JSON (e.g. ``"HIGH"``) and compare equal to those strings.
"""

from enum import StrEnum


class RiskLevel(StrEnum):
    """Severity band produced by risk assessment."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PolicyDecision(StrEnum):
    """Automated policy outcome for a proposed payment."""

    ALLOW = "ALLOW"
    """Payment may proceed without human involvement."""
    REVIEW = "REVIEW"
    """Payment must be approved by a human before proceeding."""
    BLOCK = "BLOCK"
    """Payment is refused outright."""


class ApprovalDecision(StrEnum):
    """Decision recorded by a human reviewer."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class TransactionStatus(StrEnum):
    """Lifecycle status of a transaction."""

    PENDING_REVIEW = "PENDING_REVIEW"
    """Awaiting a human approval decision."""
    APPROVED = "APPROVED"
    """Allowed by policy or approved by a human; not yet executed."""
    REJECTED = "REJECTED"
    """Rejected by a human reviewer."""
    BLOCKED = "BLOCKED"
    """Blocked automatically by policy."""
    COMPLETED = "COMPLETED"
    """Payment executed successfully with the payment provider."""
    FAILED = "FAILED"
    """Payment execution was attempted and failed."""


class PaymentStatus(StrEnum):
    """Payment workflow status (PayPal Orders v2: authorize -> human-approved capture).

    Transitions are enforced server-side (``app.services.payments.records``);
    API clients can never set a status directly.
    """

    BLOCKED = "BLOCKED"
    """Risk policy decided BLOCK. Terminal; never sent to PayPal."""
    PENDING_REVIEW = "PENDING_REVIEW"
    """Risk policy decided REVIEW. Awaiting a reviewer; nothing sent to PayPal."""
    REJECTED = "REJECTED"
    """A reviewer rejected the payment. Terminal; never sent to PayPal."""
    APPROVED_FOR_PAYMENT = "APPROVED_FOR_PAYMENT"
    """Allowed by policy or approved by a reviewer; PayPal order not yet confirmed."""
    AWAITING_BUYER_APPROVAL = "AWAITING_BUYER_APPROVAL"
    """AUTHORIZE-intent PayPal order created; the buyer must approve it on PayPal."""
    BUYER_APPROVED = "BUYER_APPROVED"
    """PayPal reported the order APPROVED; authorization requested but not yet confirmed."""
    AUTHORIZATION_PENDING = "AUTHORIZATION_PENDING"
    """PayPal reported the authorization as PENDING. Not capturable yet."""
    AUTHORIZED = "AUTHORIZED"
    """Funds authorized (authorization CREATED). Awaiting explicit reviewer capture or void."""
    PENDING_CAPTURE = "PENDING_CAPTURE"
    """A reviewer requested capture; outcome not yet confirmed (retry reuses the same request ID)."""
    CAPTURE_PENDING = "CAPTURE_PENDING"
    """PayPal accepted the capture with status PENDING. Not complete."""
    CAPTURED = "CAPTURED"
    """PayPal reported the capture COMPLETED. Terminal."""
    PENDING_VOID = "PENDING_VOID"
    """A reviewer requested a void; outcome not yet confirmed (retry reuses the same request ID)."""
    VOIDED = "VOIDED"
    """Authorization voided. Terminal."""
    FAILED = "FAILED"
    """PayPal definitively rejected or declined an operation. Terminal."""
