"""RiskAssessment validation."""

from datetime import UTC

import pytest

from app.schemas import PolicyDecision, RiskAssessment, RiskLevel
from tests.helpers import assert_invalid


def test_risk_assessment_valid(risk):
    assessment = RiskAssessment(**risk)
    assert assessment.risk_level is RiskLevel.HIGH
    assert assessment.decision is PolicyDecision.REVIEW
    assert assessment.risk_score == 72


def test_assessed_at_defaults_to_utc(risk):
    assert RiskAssessment(**risk).assessed_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("score", "error_type"),
    [
        pytest.param(101, "less_than_equal", id="above-100"),
        pytest.param(-1, "greater_than_equal", id="below-0"),
    ],
)
def test_risk_score_out_of_range_rejected(risk, score, error_type):
    assert_invalid(lambda: RiskAssessment(**{**risk, "risk_score": score}), "risk_score", error_type)


def test_unknown_risk_level_rejected(risk):
    assert_invalid(lambda: RiskAssessment(**{**risk, "risk_level": "SEVERE"}), "risk_level", "enum")


def test_unknown_decision_rejected(risk):
    assert_invalid(lambda: RiskAssessment(**{**risk, "decision": "MAYBE"}), "decision", "enum")


def test_empty_reasons_rejected(risk):
    assert_invalid(lambda: RiskAssessment(**{**risk, "reasons": []}), "reasons", "too_short")


def test_policy_decision_values():
    assert [m.value for m in PolicyDecision] == ["ALLOW", "REVIEW", "BLOCK"]
