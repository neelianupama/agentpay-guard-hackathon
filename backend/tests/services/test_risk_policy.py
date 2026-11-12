"""RiskPolicy defaults, score bands and validation of unsafe configurations."""

from decimal import Decimal

import pytest

from app.schemas import PolicyDecision, RiskLevel
from app.services.risk import RiskPolicy

ALLOW, REVIEW, BLOCK = PolicyDecision.ALLOW, PolicyDecision.REVIEW, PolicyDecision.BLOCK
LEVELS = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


def test_default_policy_is_valid():
    policy = RiskPolicy()
    assert (policy.medium_threshold, policy.high_threshold, policy.critical_threshold) == (25, 50, 75)
    assert dict(policy.level_decisions) == {
        RiskLevel.LOW: ALLOW,
        RiskLevel.MEDIUM: REVIEW,
        RiskLevel.HIGH: REVIEW,
        RiskLevel.CRITICAL: BLOCK,
    }
    assert policy.supported_currencies == {"USD", "EUR", "GBP", "CAD", "AUD", "INR"}
    assert policy.blocked_merchants == frozenset()


@pytest.mark.parametrize(
    ("score", "level"),
    [
        (0, RiskLevel.LOW),
        (24, RiskLevel.LOW),
        (25, RiskLevel.MEDIUM),
        (49, RiskLevel.MEDIUM),
        (50, RiskLevel.HIGH),
        (74, RiskLevel.HIGH),
        (75, RiskLevel.CRITICAL),
        (100, RiskLevel.CRITICAL),
    ],
)
def test_level_for_score_boundaries(score, level):
    assert RiskPolicy().level_for_score(score) is level


def test_near_threshold_floor():
    assert RiskPolicy().near_threshold_floor == Decimal("1800.00")


def test_policy_is_immutable():
    with pytest.raises(AttributeError):
        RiskPolicy().max_amount = Decimal("1")  # type: ignore[misc]


def _mapping(**overrides):
    base = {RiskLevel.LOW: ALLOW, RiskLevel.MEDIUM: REVIEW, RiskLevel.HIGH: REVIEW, RiskLevel.CRITICAL: BLOCK}
    base.update({RiskLevel[k]: v for k, v in overrides.items()})
    return base


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param({"medium_threshold": 0}, "thresholds", id="medium-zero"),
        pytest.param({"medium_threshold": 60}, "thresholds", id="medium-above-high"),
        pytest.param({"high_threshold": 80}, "thresholds", id="high-above-critical"),
        pytest.param({"critical_threshold": 101}, "thresholds", id="critical-above-100"),
        pytest.param({"level_decisions": _mapping(HIGH=ALLOW)}, "HIGH risk must never map to ALLOW", id="high-allow"),
        pytest.param(
            {"level_decisions": _mapping(HIGH=BLOCK, CRITICAL=ALLOW)},
            "CRITICAL risk must never map to ALLOW",
            id="critical-allow",
        ),
        pytest.param({"level_decisions": _mapping(MEDIUM=BLOCK)}, "less strict", id="non-monotonic"),
        pytest.param(
            {"level_decisions": {RiskLevel.LOW: ALLOW, RiskLevel.MEDIUM: REVIEW}},
            "missing levels",
            id="missing-levels",
        ),
        pytest.param({"elevated_amount": Decimal("0")}, "amounts", id="elevated-zero"),
        pytest.param({"review_amount": Decimal("400")}, "amounts", id="review-below-elevated"),
        pytest.param({"max_amount": Decimal("2000.00")}, "amounts", id="max-equals-review"),
        pytest.param({"near_threshold_ratio": Decimal("1")}, "near_threshold_ratio", id="ratio-one"),
        pytest.param({"near_threshold_ratio": Decimal("-0.1")}, "near_threshold_ratio", id="ratio-negative"),
        pytest.param({"urgency_points": 101}, "urgency_points", id="points-above-100"),
        pytest.param({"vague_details_points": -1}, "vague_details_points", id="points-negative"),
        pytest.param({"supported_currencies": frozenset()}, "supported_currencies", id="no-currencies"),
        pytest.param({"supported_currencies": frozenset({"usd"})}, "supported_currencies", id="lowercase-currency"),
        pytest.param({"min_detail_length": 0}, "min_detail_length", id="detail-length-zero"),
    ],
)
def test_unsafe_policy_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RiskPolicy(**kwargs)


def test_string_mapping_to_allow_is_still_rejected():
    with pytest.raises(ValueError, match="HIGH risk must never map to ALLOW"):
        RiskPolicy(level_decisions={**_mapping(), RiskLevel.HIGH: "ALLOW"})


def test_stricter_custom_policy_is_accepted():
    policy = RiskPolicy(
        level_decisions=_mapping(LOW=REVIEW),
        max_amount=Decimal("5000.00"),
        blocked_merchants=frozenset({"Bad Co"}),
    )
    assert policy.level_decisions[RiskLevel.LOW] is REVIEW
