"""Risk assessment contract."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from app.schemas.common import APIModel, UtcDatetime, utc_now
from app.schemas.enums import PolicyDecision, RiskLevel
from app.schemas.payment import TransactionDetails

RiskReason = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class RiskAssessment(APIModel):
    """Outcome of evaluating a proposed payment against risk policy.

    This schema only describes the shape of an assessment. How the score,
    level and decision are derived is not defined here.
    """

    transaction: TransactionDetails = Field(description="The payment being assessed.")
    risk_score: int = Field(
        ge=0,
        le=100,
        description="Risk score from 0 (no risk) to 100 (maximum risk).",
        examples=[72],
    )
    risk_level: RiskLevel
    decision: PolicyDecision
    reasons: list[RiskReason] = Field(
        min_length=1,
        description="Human-readable explanations for the score and decision.",
        examples=[["Amount exceeds the per-transaction limit for this agent"]],
    )
    assessed_at: UtcDatetime = Field(default_factory=utc_now)
