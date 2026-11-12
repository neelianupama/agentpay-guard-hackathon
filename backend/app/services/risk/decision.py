"""Policy decision ordering and final-decision resolution.

Decisions are ordered by strictness: ``ALLOW < REVIEW < BLOCK``. Combining
decisions always yields the strictest one, so any input can only make the
outcome stricter, never more permissive.
"""

from __future__ import annotations

from app.schemas.enums import PolicyDecision

DECISION_SEVERITY: dict[PolicyDecision, int] = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.REVIEW: 1,
    PolicyDecision.BLOCK: 2,
}


def strictest(*decisions: PolicyDecision | None) -> PolicyDecision:
    """Return the strictest of the given decisions, ignoring ``None``.

    Raises ``ValueError`` if no decision is given.
    """
    present = [d for d in decisions if d is not None]
    if not present:
        raise ValueError("at least one decision is required")
    return max(present, key=DECISION_SEVERITY.__getitem__)


def resolve_final_decision(
    policy_decision: PolicyDecision,
    agent_recommendation: PolicyDecision | None = None,
) -> PolicyDecision:
    """Combine the deterministic policy decision with an optional AI agent recommendation.

    The deterministic policy decision is authoritative. An AI agent may only
    *escalate* it (e.g. ALLOW -> REVIEW); it can never relax it. In particular
    an agent can never turn a BLOCK or REVIEW into ALLOW, or a BLOCK into REVIEW.
    """
    return strictest(policy_decision, agent_recommendation)
