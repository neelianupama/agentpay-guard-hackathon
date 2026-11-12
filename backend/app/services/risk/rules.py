"""Deterministic risk rules.

Each rule is a pure function ``(details, policy) -> RuleHit | None``. Rules
never call external services or LLMs and never depend on time or randomness,
so identical inputs always produce identical hits in the same order.

Rule catalogue (codes):

==========================  ==============================================  ===========
Code                        Triggers when                                   Min decision
==========================  ==============================================  ===========
AMOUNT_OVER_LIMIT           amount > maximum threshold *                    BLOCK
AMOUNT_REVIEW_THRESHOLD     review <= amount <= maximum *                   REVIEW
AMOUNT_ELEVATED             elevated <= amount < review *                   -
AMOUNT_NEAR_THRESHOLD       amount just below review threshold *            -
CURRENCY_UNSUPPORTED        currency not in supported_currencies            REVIEW
CURRENCY_THRESHOLDS_NOT_    supported currency with no configured           REVIEW
  CONFIGURED                amount thresholds
MERCHANT_BLOCKED           merchant on configured deny list                BLOCK
MERCHANT_NAME_INVALID       merchant is a placeholder or has no letters     -
HIGH_RISK_KEYWORDS          high-risk payment category terms in text        REVIEW
URGENCY_LANGUAGE            pressure / urgency phrases in text              -
POLICY_OVERRIDE_ATTEMPT     text tries to bypass or pre-empt approval       BLOCK
DETAILS_VAGUE               description/purpose too short or placeholder    -
==========================  ==============================================  ===========

The three amount tiers are mutually exclusive: only the highest applies.

\\* Amount rules use the thresholds configured for the transaction's own
currency (``RiskPolicy.thresholds_for``). If none are configured they do not
run at all: amounts are never compared against another currency's limits.
Instead CURRENCY_THRESHOLDS_NOT_CONFIGURED (supported currency) or
CURRENCY_UNSUPPORTED forces REVIEW.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.schemas.enums import PolicyDecision
from app.schemas.payment import PaymentIntent, TransactionDetails
from app.services.risk.policy import RiskPolicy


@dataclass(frozen=True)
class RuleHit:
    """A triggered rule: its code, score contribution, explanation and decision floor."""

    code: str
    points: int
    reason: str
    min_decision: PolicyDecision | None = None


Rule = Callable[[TransactionDetails, RiskPolicy], RuleHit | None]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, replace punctuation with spaces and collapse whitespace."""
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _money(amount: Decimal, currency: str | None = None) -> str:
    formatted = f"{amount:,.2f}"
    return f"{formatted} {currency}" if currency else formatted


def _text_fields(details: TransactionDetails) -> list[tuple[str, str]]:
    """Free-text fields scanned for keywords, in a fixed order."""
    fields = [
        ("merchant name", details.merchant_name),
        ("description", details.description),
        ("purpose", details.purpose),
    ]
    if isinstance(details, PaymentIntent):
        fields.append(("request text", details.request_text))
    return fields


def _find_phrases(
    details: TransactionDetails, phrases: Iterable[str]
) -> tuple[list[str], list[str]]:
    """Return (matched phrases sorted, field labels in fixed order) for whole-word matches."""
    normalized_phrases = {normalize(p): p for p in phrases if normalize(p)}
    matched: set[str] = set()
    fields: list[str] = []
    for label, value in _text_fields(details):
        padded = f" {normalize(value)} "
        hits = {orig for norm, orig in normalized_phrases.items() if f" {norm} " in padded}
        if hits:
            matched |= hits
            fields.append(label)
    return sorted(matched), fields


def _quoted(items: Iterable[str]) -> str:
    return ", ".join(f"'{item}'" for item in items)


# --------------------------------------------------------------------------- amount


def amount_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    thresholds = policy.thresholds_for(details.currency)
    if thresholds is None:
        return None  # never compare against another currency's limits
    amount, currency = details.amount, details.currency
    shown = _money(amount, currency)
    if thresholds.exceeds_maximum(amount):
        relation = "is at or above" if thresholds.block_at_maximum else "exceeds"
        return RuleHit(
            "AMOUNT_OVER_LIMIT",
            policy.over_limit_points,
            f"Amount {shown} {relation} the maximum allowed transaction amount "
            f"of {_money(thresholds.maximum, currency)}.",
            PolicyDecision.BLOCK,
        )
    if amount >= thresholds.review:
        return RuleHit(
            "AMOUNT_REVIEW_THRESHOLD",
            policy.review_amount_points,
            f"Amount {shown} is at or above the human-review threshold "
            f"of {_money(thresholds.review, currency)}.",
            PolicyDecision.REVIEW,
        )
    if amount >= thresholds.elevated:
        return RuleHit(
            "AMOUNT_ELEVATED",
            policy.elevated_amount_points,
            f"Amount {shown} is at or above the elevated-amount threshold "
            f"of {_money(thresholds.elevated, currency)}.",
        )
    return None


def near_threshold_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    thresholds = policy.thresholds_for(details.currency)
    if thresholds is None or policy.near_threshold_ratio == 0:
        return None
    floor = thresholds.near_threshold_floor(policy.near_threshold_ratio)
    if floor <= details.amount < thresholds.review:
        return RuleHit(
            "AMOUNT_NEAR_THRESHOLD",
            policy.near_threshold_points,
            f"Amount {_money(details.amount, details.currency)} is just below the "
            f"human-review threshold of {_money(thresholds.review, details.currency)}, "
            "which can indicate a payment sized to avoid review.",
        )
    return None


# ------------------------------------------------------------------------- currency


def currency_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    if details.currency in policy.supported_currencies:
        return None
    return RuleHit(
        "CURRENCY_UNSUPPORTED",
        policy.unsupported_currency_points,
        f"Currency '{details.currency}' is not a supported currency "
        f"({', '.join(sorted(policy.supported_currencies))}); the amount cannot "
        "be risk-assessed, so human review is required.",
        PolicyDecision.REVIEW,
    )


def currency_thresholds_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    currency = details.currency
    if currency not in policy.supported_currencies:
        return None  # handled by CURRENCY_UNSUPPORTED
    if policy.thresholds_for(currency) is not None:
        return None
    return RuleHit(
        "CURRENCY_THRESHOLDS_NOT_CONFIGURED",
        policy.unconfigured_currency_points,
        f"No amount thresholds are configured for {currency}. Amounts are not "
        f"converted or compared against {policy.default_currency} limits, so a "
        f"{currency} payment requires human review.",
        PolicyDecision.REVIEW,
    )


# ------------------------------------------------------------------------- merchant


def blocked_merchant_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    blocked = {normalize(m) for m in policy.blocked_merchants}
    if normalize(details.merchant_name) in blocked:
        return RuleHit(
            "MERCHANT_BLOCKED",
            policy.blocked_merchant_points,
            f"Merchant '{details.merchant_name}' is on the configured blocked-merchant list.",
            PolicyDecision.BLOCK,
        )
    return None


def merchant_name_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    name = details.merchant_name
    normalized = normalize(name)
    if not any(ch.isalpha() for ch in name):
        problem = "contains no letters"
    elif normalized in {normalize(p) for p in policy.merchant_placeholders}:
        problem = "is a generic placeholder"
    else:
        return None
    return RuleHit(
        "MERCHANT_NAME_INVALID",
        policy.malformed_merchant_points,
        f"Merchant name '{name}' {problem} and does not identify a specific payee.",
    )


# ----------------------------------------------------------------------------- text


def high_risk_keyword_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    matched, fields = _find_phrases(details, policy.high_risk_keywords)
    if not matched:
        return None
    return RuleHit(
        "HIGH_RISK_KEYWORDS",
        policy.high_risk_keyword_points,
        f"High-risk payment terms {_quoted(matched)} found in {', '.join(fields)}.",
        PolicyDecision.REVIEW,
    )


def urgency_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    matched, fields = _find_phrases(details, policy.urgency_keywords)
    if not matched:
        return None
    return RuleHit(
        "URGENCY_LANGUAGE",
        policy.urgency_points,
        f"Urgency or pressure language {_quoted(matched)} found in {', '.join(fields)}.",
    )


def override_attempt_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    matched, fields = _find_phrases(details, policy.override_phrases)
    if not matched:
        return None
    return RuleHit(
        "POLICY_OVERRIDE_ATTEMPT",
        policy.override_attempt_points,
        f"Text in {', '.join(fields)} attempts to bypass or pre-empt policy "
        f"controls ({_quoted(matched)}); approval cannot be granted by transaction content.",
        PolicyDecision.BLOCK,
    )


def vague_details_rule(details: TransactionDetails, policy: RiskPolicy) -> RuleHit | None:
    placeholders = {normalize(p) for p in policy.detail_placeholders}
    problems: list[str] = []
    for label, value in (("description", details.description), ("purpose", details.purpose)):
        normalized = normalize(value)
        if not normalized or normalized in placeholders or len(value) < policy.min_detail_length:
            problems.append(f"{label} ('{value}')")
    if not problems:
        return None
    return RuleHit(
        "DETAILS_VAGUE",
        policy.vague_details_points,
        f"Missing, too short or placeholder transaction details: {', '.join(problems)}; "
        "the payment's intent cannot be confirmed.",
    )


DEFAULT_RULES: tuple[Rule, ...] = (
    amount_rule,
    near_threshold_rule,
    currency_rule,
    currency_thresholds_rule,
    blocked_merchant_rule,
    merchant_name_rule,
    high_risk_keyword_rule,
    urgency_rule,
    override_attempt_rule,
    vague_details_rule,
)
"""All rules, in the fixed order their reasons are reported."""
