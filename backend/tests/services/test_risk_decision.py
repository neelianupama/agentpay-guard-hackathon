"""Decision ordering and AI-agent non-override guarantee."""

import pytest

from app.schemas import PolicyDecision
from app.services.risk import resolve_final_decision, strictest

ALLOW, REVIEW, BLOCK = PolicyDecision.ALLOW, PolicyDecision.REVIEW, PolicyDecision.BLOCK


@pytest.mark.parametrize(
    ("decisions", "expected"),
    [
        ((ALLOW,), ALLOW),
        ((ALLOW, REVIEW), REVIEW),
        ((REVIEW, ALLOW), REVIEW),
        ((ALLOW, BLOCK, REVIEW), BLOCK),
        ((None, REVIEW, None), REVIEW),
    ],
)
def test_strictest(decisions, expected):
    assert strictest(*decisions) is expected


def test_strictest_requires_a_decision():
    with pytest.raises(ValueError):
        strictest(None)


@pytest.mark.parametrize(
    ("policy", "agent", "expected"),
    [
        (ALLOW, None, ALLOW),
        (ALLOW, ALLOW, ALLOW),
        (ALLOW, REVIEW, REVIEW),
        (ALLOW, BLOCK, BLOCK),
        (REVIEW, None, REVIEW),
        (REVIEW, ALLOW, REVIEW),
        (REVIEW, REVIEW, REVIEW),
        (REVIEW, BLOCK, BLOCK),
        (BLOCK, None, BLOCK),
        (BLOCK, ALLOW, BLOCK),
        (BLOCK, REVIEW, BLOCK),
        (BLOCK, BLOCK, BLOCK),
    ],
)
def test_resolve_final_decision_matrix(policy, agent, expected):
    assert resolve_final_decision(policy, agent) is expected


@pytest.mark.parametrize("policy", [REVIEW, BLOCK])
@pytest.mark.parametrize("agent", [ALLOW, REVIEW, BLOCK, None])
def test_agent_can_never_relax_review_or_block(policy, agent):
    final = resolve_final_decision(policy, agent)
    assert final is not ALLOW
    if policy is BLOCK:
        assert final is BLOCK
