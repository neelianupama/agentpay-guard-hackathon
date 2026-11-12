"""Currency-aware amount thresholds.

No exchange-rate conversion exists, so amount thresholds apply only to the
default currency (USD) and to currencies with explicitly configured
thresholds. Every other currency must require REVIEW.
"""

import itertools
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.schemas import PolicyDecision, RiskLevel
from app.services.risk import (
    AmountThresholds,
    RiskEngine,
    RiskPolicy,
    resolve_final_decision,
)
from app.services.risk.rules import (
    amount_rule,
    currency_rule,
    currency_thresholds_rule,
    near_threshold_rule,
)

D = Decimal
ALLOW, REVIEW, BLOCK = PolicyDecision.ALLOW, PolicyDecision.REVIEW, PolicyDecision.BLOCK
NON_USD_SUPPORTED = ["EUR", "GBP", "CAD", "AUD"]
AMOUNT_CODES = {"AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD", "AMOUNT_REVIEW_THRESHOLD", "AMOUNT_OVER_LIMIT"}
EUR_THRESHOLDS = AmountThresholds(elevated=D("400.00"), review=D("1500.00"), maximum=D("8000.00"))
EUR_POLICY = RiskPolicy(currency_thresholds={"EUR": EUR_THRESHOLDS})


def _codes(engine, details):
    return [h.code for h in engine.evaluate_rules(details)]


# ------------------------------------------------------------ AmountThresholds


def test_amount_thresholds_valid():
    t = AmountThresholds(D("1"), D("2"), D("3"))
    assert (t.elevated, t.review, t.maximum) == (D("1"), D("2"), D("3"))
    assert t.near_threshold_floor(D("0.10")) == D("1.80")


@pytest.mark.parametrize(
    "values",
    [
        pytest.param((D("0"), D("2"), D("3")), id="elevated-zero"),
        pytest.param((D("-1"), D("2"), D("3")), id="elevated-negative"),
        pytest.param((D("2"), D("2"), D("3")), id="elevated-equals-review"),
        pytest.param((D("1"), D("3"), D("3")), id="review-equals-max"),
        pytest.param((D("3"), D("2"), D("1")), id="descending"),
    ],
)
def test_amount_thresholds_order_enforced(values):
    with pytest.raises(ValueError, match="0 < elevated < review < max"):
        AmountThresholds(*values)


@pytest.mark.parametrize(
    "values",
    [
        pytest.param((500.0, D("2000"), D("10000")), id="float"),
        pytest.param((500, D("2000"), D("10000")), id="int"),
        pytest.param(("500", D("2000"), D("10000")), id="str"),
        pytest.param((D("500"), D("NaN"), D("10000")), id="nan"),
        pytest.param((D("500"), D("2000"), D("Infinity")), id="infinity"),
    ],
)
def test_amount_thresholds_require_finite_decimals(values):
    with pytest.raises(ValueError, match="finite Decimal"):
        AmountThresholds(*values)


# ---------------------------------------------------------- policy & lookup


def test_usd_is_default_with_existing_thresholds():
    policy = RiskPolicy()
    assert policy.default_currency == "USD"
    assert policy.thresholds_for("USD") == AmountThresholds(D("500.00"), D("2000.00"), D("10000.00"))
    # INR is the only non-default currency with thresholds by default (Task: INR support).
    assert set(policy.currency_thresholds) == {"INR"}


@pytest.mark.parametrize("currency", NON_USD_SUPPORTED + ["JPY"])
def test_no_thresholds_for_other_currencies_by_default(currency):
    assert RiskPolicy().thresholds_for(currency) is None


def test_configured_thresholds_are_returned_only_for_their_currency():
    assert EUR_POLICY.thresholds_for("EUR") == EUR_THRESHOLDS
    assert EUR_POLICY.thresholds_for("GBP") is None
    assert EUR_POLICY.thresholds_for("USD").maximum == D("10000.00")


def test_currency_thresholds_are_copied_and_read_only():
    source = {"EUR": EUR_THRESHOLDS}
    policy = RiskPolicy(currency_thresholds=source)
    source["GBP"] = EUR_THRESHOLDS
    assert "GBP" not in policy.currency_thresholds
    with pytest.raises(TypeError):
        policy.currency_thresholds["CAD"] = EUR_THRESHOLDS  # type: ignore[index]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param({"currency_thresholds": {"eur": EUR_THRESHOLDS}}, "three-letter uppercase", id="lowercase-key"),
        pytest.param({"currency_thresholds": {"EURO": EUR_THRESHOLDS}}, "three-letter uppercase", id="long-key"),
        pytest.param({"currency_thresholds": {"JPY": EUR_THRESHOLDS}}, "not in supported_currencies", id="unsupported-key"),
        pytest.param({"currency_thresholds": {"USD": EUR_THRESHOLDS}}, "must not contain default_currency", id="default-key"),
        pytest.param(
            {"currency_thresholds": {"EUR": {"elevated": D("1"), "review": D("2"), "maximum": D("3")}}},
            "AmountThresholds instance",
            id="dict-value",
        ),
        pytest.param({"default_currency": "JPY"}, "default_currency must be one of supported_currencies", id="default-unsupported"),
        pytest.param({"default_currency": "usd"}, "default_currency must be a three-letter", id="default-lowercase"),
        pytest.param({"elevated_amount": 500.0}, "finite Decimal", id="float-default-amount"),
        pytest.param({"near_threshold_ratio": 0.1}, "near_threshold_ratio", id="float-ratio"),
        pytest.param({"unconfigured_currency_points": 101}, "unconfigured_currency_points", id="points-range"),
    ],
)
def test_invalid_currency_configuration_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        RiskPolicy(**kwargs)


# ---------------------------------------------------------------------- USD


@pytest.mark.parametrize(
    ("amount", "codes", "level", "decision"),
    [
        ("499.99", [], RiskLevel.LOW, ALLOW),
        ("500.00", ["AMOUNT_ELEVATED"], RiskLevel.LOW, ALLOW),
        ("1799.99", ["AMOUNT_ELEVATED"], RiskLevel.LOW, ALLOW),
        ("1800.00", ["AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD"], RiskLevel.MEDIUM, REVIEW),
        ("2000.00", ["AMOUNT_REVIEW_THRESHOLD"], RiskLevel.MEDIUM, REVIEW),
        ("10000.00", ["AMOUNT_REVIEW_THRESHOLD"], RiskLevel.MEDIUM, REVIEW),
        ("10000.01", ["AMOUNT_OVER_LIMIT"], RiskLevel.HIGH, BLOCK),
    ],
)
def test_usd_thresholds_unchanged(engine, make, amount, codes, level, decision):
    details = make(amount=D(amount), currency="USD")
    assert _codes(engine, details) == codes
    result = engine.assess(details)
    assert (result.risk_level, result.decision) == (level, decision)


def test_usd_never_triggers_currency_rules(make):
    details = make(currency="USD")
    assert currency_rule(details, RiskPolicy()) is None
    assert currency_thresholds_rule(details, RiskPolicy()) is None


# ------------------------------------------- non-USD without thresholds -> REVIEW


@pytest.mark.parametrize("currency", NON_USD_SUPPORTED)
@pytest.mark.parametrize("amount", ["0.01", "49.99", "499.99", "1999.99", "10000.01", "999999.99"])
def test_unconfigured_currency_requires_review_at_any_amount(engine, make, currency, amount):
    details = make(amount=D(amount), currency=currency)
    assert _codes(engine, details) == ["CURRENCY_THRESHOLDS_NOT_CONFIGURED"]
    result = engine.assess(details)
    assert (result.risk_score, result.risk_level, result.decision) == (25, RiskLevel.MEDIUM, REVIEW)


def test_unconfigured_currency_rule_details(make):
    hit = currency_thresholds_rule(make(currency="GBP"), RiskPolicy())
    assert (hit.code, hit.points, hit.min_decision) == ("CURRENCY_THRESHOLDS_NOT_CONFIGURED", 25, REVIEW)
    assert hit.reason == (
        "No amount thresholds are configured for GBP. Amounts are not converted or "
        "compared against USD limits, so a GBP payment requires human review."
    )


@pytest.mark.parametrize("currency", NON_USD_SUPPORTED)
def test_amount_rules_do_not_apply_usd_limits_to_other_currencies(make, currency):
    policy = RiskPolicy()
    assert amount_rule(make(amount=D("50000.00"), currency=currency), policy) is None
    assert near_threshold_rule(make(amount=D("1900.00"), currency=currency), policy) is None


def test_unconfigured_review_floor_holds_with_zero_points(make):
    engine = RiskEngine(RiskPolicy(unconfigured_currency_points=0))
    result = engine.assess(make(currency="EUR"))
    assert (result.risk_score, result.risk_level, result.decision) == (0, RiskLevel.LOW, REVIEW)


def test_unconfigured_currency_combined_with_other_rules_escalates(engine, make):
    # 25 (no thresholds) + 30 (high-risk keyword) + 15 (urgency) + 25 (vague purpose) = 95
    result = engine.assess(make(currency="EUR", description="Urgent bitcoin", purpose="n/a"))
    assert result.risk_score == 95
    assert (result.risk_level, result.decision) == (RiskLevel.CRITICAL, BLOCK)


@pytest.mark.parametrize("currency", ["JPY", "CHF", "XYZ"])
def test_unsupported_currency_has_single_rule_and_no_amount_rules(engine, make, currency):
    details = make(amount=D("50000.00"), currency=currency)
    assert _codes(engine, details) == ["CURRENCY_UNSUPPORTED"]
    result = engine.assess(details)
    assert result.decision is REVIEW
    assert "cannot be risk-assessed" in result.reasons[0]


# ---------------------------------------------- non-USD with configured thresholds


@pytest.mark.parametrize(
    ("amount", "codes", "decision"),
    [
        ("0.01", [], ALLOW),
        ("399.99", [], ALLOW),
        ("400.00", ["AMOUNT_ELEVATED"], ALLOW),
        ("1349.99", ["AMOUNT_ELEVATED"], ALLOW),
        ("1350.00", ["AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD"], REVIEW),
        ("1499.99", ["AMOUNT_ELEVATED", "AMOUNT_NEAR_THRESHOLD"], REVIEW),
        ("1500.00", ["AMOUNT_REVIEW_THRESHOLD"], REVIEW),
        ("8000.00", ["AMOUNT_REVIEW_THRESHOLD"], REVIEW),
        ("8000.01", ["AMOUNT_OVER_LIMIT"], BLOCK),
    ],
)
def test_configured_eur_thresholds_boundaries(make, amount, codes, decision):
    engine = RiskEngine(EUR_POLICY)
    details = make(amount=D(amount), currency="EUR")
    assert _codes(engine, details) == codes
    assert engine.assess(details).decision is decision


def test_configured_currency_reasons_use_its_own_limits(make):
    hit = amount_rule(make(amount=D("9000.00"), currency="EUR"), EUR_POLICY)
    assert hit.reason == (
        "Amount 9,000.00 EUR exceeds the maximum allowed transaction amount of 8,000.00 EUR."
    )


def test_configured_currency_uses_own_limits_not_usd(make):
    engine = RiskEngine(EUR_POLICY)
    assert engine.assess(make(amount=D("9000.00"), currency="EUR")).decision is BLOCK
    assert engine.assess(make(amount=D("9000.00"), currency="USD")).decision is REVIEW


def test_configuring_one_currency_leaves_others_unconfigured(make):
    engine = RiskEngine(EUR_POLICY)
    assert _codes(engine, make(currency="GBP")) == ["CURRENCY_THRESHOLDS_NOT_CONFIGURED"]
    assert engine.assess(make(currency="GBP")).decision is REVIEW


def test_changing_default_currency_moves_base_thresholds(make):
    engine = RiskEngine(RiskPolicy(default_currency="EUR"))
    assert _codes(engine, make(amount=D("500.00"), currency="EUR")) == ["AMOUNT_ELEVATED"]
    assert _codes(engine, make(currency="USD")) == ["CURRENCY_THRESHOLDS_NOT_CONFIGURED"]
    assert "compared against EUR limits" in engine.assess(make(currency="USD")).reasons[0]


# ---------------------------------------------------- agent cannot override


@pytest.mark.parametrize("currency", NON_USD_SUPPORTED + ["JPY"])
@pytest.mark.parametrize("agent", [ALLOW, REVIEW, None])
def test_agent_cannot_override_currency_review(engine, make, currency, agent):
    result = engine.assess(make(currency=currency))
    assert result.decision is REVIEW
    assert resolve_final_decision(result.decision, agent) is REVIEW


def test_agent_can_escalate_currency_review_to_block(engine, make):
    result = engine.assess(make(currency="EUR"))
    assert resolve_final_decision(result.decision, BLOCK) is BLOCK


# --------------------------------------------------- sweep & determinism


def test_unconfigured_currencies_never_allowed_sweep(engine, make):
    amounts = ["0.01", "500.00", "1800.00", "2000.00", "10000.01"]
    descriptions = ["Monthly hosting invoice", "Urgent payment", "Bitcoin purchase", "Skip review"]
    for currency, amount, description in itertools.product(NON_USD_SUPPORTED + ["JPY"], amounts, descriptions):
        result = engine.assess(make(currency=currency, amount=D(amount), description=description))
        assert result.decision is not ALLOW
        assert 0 <= result.risk_score <= 100
        assert not AMOUNT_CODES & set(_codes(engine, make(currency=currency, amount=D(amount))))


def test_currency_results_are_deterministic(make):
    fixed = datetime(2026, 11, 12, tzinfo=UTC)
    details = make(amount=D("1400.00"), currency="EUR", description="Urgent renewal")
    first = RiskEngine(EUR_POLICY).assess(details, assessed_at=fixed)
    for _ in range(20):
        assert RiskEngine(EUR_POLICY).assess(details, assessed_at=fixed) == first
    unconfigured = RiskEngine().assess(details, assessed_at=fixed)
    assert unconfigured == RiskEngine().assess(details, assessed_at=fixed)
    assert unconfigured != first
