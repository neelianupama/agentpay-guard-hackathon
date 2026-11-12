"""Deterministic risk assessment engine.

Pipeline::

    TransactionDetails -> rules -> score (clamped 0-100) -> risk level
                       -> decision = strictest(level decision, rule minimums)
                       -> RiskAssessment

No LLM, network call, clock or randomness influences the score, level,
decision or reasons. ``assessed_at`` is the only time-dependent field and
can be pinned by the caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.schemas.enums import PolicyDecision, RiskLevel
from app.schemas.payment import TransactionDetails
from app.schemas.risk import RiskAssessment
from app.services.risk.decision import strictest
from app.services.risk.policy import RiskPolicy
from app.services.risk.rules import DEFAULT_RULES, Rule, RuleHit

MAX_REASON_LENGTH = 500  # matches RiskAssessment.reasons item constraint

NO_RULES_TRIGGERED_REASON = (
    "No risk rules were triggered. Merchant identity has not been independently verified."
)


def clamp_score(raw: int) -> int:
    return max(0, min(100, raw))


def _truncate(reason: str) -> str:
    if len(reason) <= MAX_REASON_LENGTH:
        return reason
    return reason[: MAX_REASON_LENGTH - 1] + "…"


class RiskEngine:
    """Evaluates transactions against a :class:`RiskPolicy`. Stateless and thread-safe."""

    def __init__(self, policy: RiskPolicy | None = None, rules: Sequence[Rule] = DEFAULT_RULES):
        self.policy = policy or RiskPolicy()
        self.rules: tuple[Rule, ...] = tuple(rules)

    def evaluate_rules(self, details: TransactionDetails) -> tuple[RuleHit, ...]:
        """Run every rule and return the triggered ones in rule order."""
        hits = (rule(details, self.policy) for rule in self.rules)
        return tuple(hit for hit in hits if hit is not None)

    def score(self, hits: Sequence[RuleHit]) -> int:
        return clamp_score(sum(hit.points for hit in hits))

    def decide(self, level: RiskLevel, hits: Sequence[RuleHit]) -> PolicyDecision:
        return strictest(self.policy.level_decisions[level], *(h.min_decision for h in hits))

    def assess(
        self,
        details: TransactionDetails,
        *,
        assessed_at: datetime | None = None,
    ) -> RiskAssessment:
        """Assess a proposed payment.

        Accepts ``TransactionDetails`` or any subclass (e.g. ``PaymentIntent``,
        whose ``request_text`` is also scanned). There is intentionally no
        parameter for overriding the decision.
        """
        hits = self.evaluate_rules(details)
        score = self.score(hits)
        level = self.policy.level_for_score(score)
        decision = self.decide(level, hits)

        reasons = [_truncate(h.reason) for h in hits] or [NO_RULES_TRIGGERED_REASON]
        transaction = TransactionDetails.model_validate(
            details.model_dump(include=set(TransactionDetails.model_fields))
        )

        extra = {"assessed_at": assessed_at} if assessed_at is not None else {}
        return RiskAssessment(
            transaction=transaction,
            risk_score=score,
            risk_level=level,
            decision=decision,
            reasons=reasons,
            **extra,
        )


def assess_transaction(
    details: TransactionDetails,
    policy: RiskPolicy | None = None,
    *,
    assessed_at: datetime | None = None,
) -> RiskAssessment:
    """Convenience wrapper: assess ``details`` with ``policy`` (defaults if omitted)."""
    return RiskEngine(policy).assess(details, assessed_at=assessed_at)
