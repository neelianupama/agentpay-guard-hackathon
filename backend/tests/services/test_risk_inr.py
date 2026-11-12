"""INR support: explicit rupee-denominated thresholds, never converted from USD.

INR thresholds (major units): elevated 40,000.00 / review 150,000.00 /
maximum 750,000.00 (inclusive: at or above the maximum is always BLOCK).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.schemas import PolicyDecision, RiskLevel
from app.services.risk import AmountThresholds, RiskEngine, RiskPolicy, resolve_final_decision
from app.services.risk.policy import DEFAULT_CURRENCY_THRESHOLDS, INR_THRESHOLDS
from app.services.risk.rules import amount_rule, currency_rule, currency_thresholds_rule

D = Decimal
ALLOW, REVIEW, BLOCK = PolicyDecision.ALLOW, PolicyDecision.REVIEW, PolicyDecision.BLOCK
LOW, MEDIUM, HIGH = RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH
AT = datetime(2026, 11, 12, tzinfo=UTC)
AMOUNT_CODES = {"AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD", "AMOUNT_REVIEW_THRESHOLD", "AMOUNT_OVER_LIMIT"}


@pytest.fixture
def default_engine():
    return RiskEngine()


@pytest.fixture
def policy():
    return RiskPolicy()


def inr(make, amount, **kwargs):
    return make(amount=D(amount), currency="INR", **kwargs)


# ------------------------------------------------------------------ configuration


def test_inr_thresholds_are_explicit_rupee_values():
    assert INR_THRESHOLDS == AmountThresholds(
        elevated=D("40000.00"),
        review=D("150000.00"),
        maximum=D("750000.00"),
        block_at_maximum=True,
    )
    assert dict(DEFAULT_CURRENCY_THRESHOLDS) == {"INR": INR_THRESHOLDS}


def test_default_policy_supports_inr_with_its_own_thresholds():
    policy = RiskPolicy()
    assert "INR" in policy.supported_currencies
    assert policy.thresholds_for("INR") is INR_THRESHOLDS


def test_usd_thresholds_and_exclusive_maximum_unchanged():
    usd = RiskPolicy().thresholds_for("USD")
    assert usd == AmountThresholds(D("500.00"), D("2000.00"), D("10000.00"))
    assert usd.block_at_maximum is False


@pytest.mark.parametrize("currency", ["EUR", "GBP", "CAD", "AUD"])
def test_other_supported_currencies_still_have_no_thresholds(currency):
    assert RiskPolicy().thresholds_for(currency) is None


def test_inr_is_not_flagged_as_unsupported_or_unconfigured(make, policy):
    details = inr(make, "100.00")
    assert currency_rule(details, policy) is None
    assert currency_thresholds_rule(details, policy) is None


# -------------------------------------------------------------- AmountThresholds


@pytest.mark.parametrize(
    ("block_at_maximum", "amount", "exceeds"),
    [
        (False, "750000.00", False),
        (False, "750000.01", True),
        (True, "749999.99", False),
        (True, "750000.00", True),
        (True, "750000.01", True),
    ],
)
def test_exceeds_maximum(block_at_maximum, amount, exceeds):
    thresholds = AmountThresholds(D("40000"), D("150000"), D("750000"), block_at_maximum)
    assert thresholds.exceeds_maximum(D(amount)) is exceeds


@pytest.mark.parametrize("value", [1, "yes", None])
def test_block_at_maximum_must_be_bool(value):
    with pytest.raises(ValueError, match="block_at_maximum"):
        AmountThresholds(D("1"), D("2"), D("3"), value)


# ------------------------------------------------------------- boundaries & levels


@pytest.mark.parametrize(
    ("amount", "codes", "score", "level", "decision"),
    [
        ("0.01", [], 0, LOW, ALLOW),
        ("39999.99", [], 0, LOW, ALLOW),
        ("40000.00", ["AMOUNT_ELEVATED"], 15, LOW, ALLOW),
        ("134999.99", ["AMOUNT_ELEVATED"], 15, LOW, ALLOW),
        ("135000.00", ["AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD"], 30, MEDIUM, REVIEW),
        ("149999.99", ["AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD"], 30, MEDIUM, REVIEW),
        ("150000.00", ["AMOUNT_REVIEW_THRESHOLD"], 35, MEDIUM, REVIEW),
        ("749999.99", ["AMOUNT_REVIEW_THRESHOLD"], 35, MEDIUM, REVIEW),
        ("750000.00", ["AMOUNT_OVER_LIMIT"], 60, HIGH, BLOCK),
        ("750000.01", ["AMOUNT_OVER_LIMIT"], 60, HIGH, BLOCK),
        ("9999999.99", ["AMOUNT_OVER_LIMIT"], 60, HIGH, BLOCK),
    ],
)
def test_inr_boundaries(default_engine, make, amount, codes, score, level, decision):
    details = inr(make, amount)
    hits = [h.code for h in default_engine.evaluate_rules(details)]
    assert [c for c in hits if c in AMOUNT_CODES] == codes
    assert not {"CURRENCY_UNSUPPORTED", "CURRENCY_THRESHOLDS_NOT_CONFIGURED"} & set(hits)
    result = default_engine.assess(details, assessed_at=AT)
    assert (result.risk_score, result.risk_level, result.decision) == (score, level, decision)


# --------------------------------------------------------------------- reasons


@pytest.mark.parametrize(
    ("amount", "reason"),
    [
        (
            "40000.00",
            "Amount 40,000.00 INR is at or above the elevated-amount threshold of 40,000.00 INR.",
        ),
        (
            "150000.00",
            "Amount 150,000.00 INR is at or above the human-review threshold of 150,000.00 INR.",
        ),
        (
            "750000.00",
            "Amount 750,000.00 INR is at or above the maximum allowed transaction amount "
            "of 750,000.00 INR.",
        ),
        (
            "800000.00",
            "Amount 800,000.00 INR is at or above the maximum allowed transaction amount "
            "of 750,000.00 INR.",
        ),
    ],
)
def test_inr_reasons_use_inr(make, policy, amount, reason):
    hit = amount_rule(inr(make, amount), policy)
    assert hit.reason == reason


def test_near_threshold_reason_uses_inr(default_engine, make):
    reasons = default_engine.assess(inr(make, "140000.00"), assessed_at=AT).reasons
    near = [r for r in reasons if "just below" in r]
    assert near == [
        "Amount 140,000.00 INR is just below the human-review threshold of 150,000.00 INR, "
        "which can indicate a payment sized to avoid review."
    ]


@pytest.mark.parametrize("amount", ["40000.00", "140000.00", "150000.00", "750000.00"])
def test_inr_reasons_never_mention_usd(default_engine, make, amount):
    reasons = default_engine.assess(inr(make, amount), assessed_at=AT).reasons
    assert all("USD" not in r for r in reasons)


def test_usd_over_limit_wording_unchanged(make, policy):
    hit = amount_rule(make(amount=D("10000.01"), currency="USD"), policy)
    assert hit.reason == (
        "Amount 10,000.01 USD exceeds the maximum allowed transaction amount of 10,000.00 USD."
    )


# ------------------------------------------------------------------ no conversion


@pytest.mark.parametrize("amount", ["600.00", "2500.00", "10000.00", "39999.99"])
def test_inr_amounts_are_not_compared_with_usd_limits(default_engine, make, amount):
    """Amounts that would trigger USD rules are LOW in rupees (no FX conversion)."""
    result = default_engine.assess(inr(make, amount), assessed_at=AT)
    assert (result.risk_score, result.decision) == (0, ALLOW)


@pytest.mark.parametrize(
    ("amount", "decision"),
    [("10000.00", REVIEW), ("10000.01", BLOCK), ("750000.00", BLOCK)],
)
def test_usd_amounts_use_usd_limits_only(default_engine, make, amount, decision):
    """USD keeps its exclusive 10,000.00 maximum; INR limits never apply to USD."""
    result = default_engine.assess(make(amount=D(amount), currency="USD"), assessed_at=AT)
    assert result.decision == decision


# ----------------------------------------------------- BLOCK at or above maximum


def test_maximum_blocks_even_when_points_alone_would_allow(make):
    """BLOCK is a rule floor, not a score outcome: zero points still blocks."""
    engine = RiskEngine(RiskPolicy(over_limit_points=0))
    result = engine.assess(inr(make, "750000.00"), assessed_at=AT)
    assert result.risk_score == 0
    assert result.risk_level == LOW
    assert result.decision == BLOCK


@pytest.mark.parametrize("recommendation", [None, ALLOW, REVIEW, BLOCK])
def test_agent_or_llm_cannot_override_inr_block(default_engine, make, recommendation):
    policy_decision = default_engine.assess(inr(make, "750000.00"), assessed_at=AT).decision
    assert policy_decision == BLOCK
    assert resolve_final_decision(policy_decision, recommendation) == BLOCK


# ----------------------------------------------------------- unsupported & custom


@pytest.mark.parametrize("currency", ["JPY", "XYZ", "CNY"])
def test_unsupported_currencies_still_require_review(default_engine, make, currency):
    result = default_engine.assess(make(currency=currency), assessed_at=AT)
    assert result.decision == REVIEW
    assert any("is not a supported currency" in r for r in result.reasons)
    assert any("INR" in r for r in result.reasons)  # listed among supported currencies


def test_explicit_thresholds_replace_defaults_and_inr_then_requires_review(make):
    eur = AmountThresholds(D("400"), D("1500"), D("8000"))
    engine = RiskEngine(RiskPolicy(currency_thresholds={"EUR": eur}))
    result = engine.assess(inr(make, "100.00"), assessed_at=AT)
    assert result.decision == REVIEW
    assert any("No amount thresholds are configured for INR" in r for r in result.reasons)


def test_policy_without_inr_treats_it_as_unsupported(make):
    policy = RiskPolicy(supported_currencies=frozenset({"USD", "EUR"}))
    assert dict(policy.currency_thresholds) == {}
    result = RiskEngine(policy).assess(inr(make, "100.00"), assessed_at=AT)
    assert result.decision == REVIEW


def test_inr_thresholds_cannot_be_configured_for_unsupported_inr():
    with pytest.raises(ValueError, match="INR is not in supported_currencies"):
        RiskPolicy(
            supported_currencies=frozenset({"USD"}),
            currency_thresholds={"INR": INR_THRESHOLDS},
        )
