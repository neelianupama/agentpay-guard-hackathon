"""Configurable risk policy: thresholds, rule weights and keyword lists.

All tunable values live in :class:`RiskPolicy`. The defaults below are the
documented baseline policy; pass a customised ``RiskPolicy`` to
``RiskEngine`` to change them. The policy validates itself on construction
so an unsafe configuration fails fast instead of silently weakening controls.

Scoring
-------
Each triggered rule adds points. The total is clamped to 0-100.

Risk levels (score bands, defaults)::

    LOW       0-24
    MEDIUM   25-49
    HIGH     50-74
    CRITICAL 75-100

Level -> decision (defaults)::

    LOW      -> ALLOW
    MEDIUM   -> REVIEW
    HIGH     -> REVIEW
    CRITICAL -> BLOCK

Some rules also impose a *minimum decision* (e.g. an over-limit amount is
always BLOCK). The final decision is the strictest of the level decision and
every triggered rule's minimum. These minimums are deliberately not
configurable: they are safety guarantees.

Safety invariants enforced by validation:

- HIGH and CRITICAL can never map to ALLOW.
- Higher risk levels can never map to a less strict decision than lower ones.

Currency-aware amount thresholds
--------------------------------
No exchange-rate conversion is performed and currencies are never assumed
to have equivalent value. Amount thresholds therefore apply only to:

- ``default_currency`` (USD by default), using ``elevated_amount``,
  ``review_amount`` and ``max_amount``; and
- currencies with an explicit :class:`AmountThresholds` entry in
  ``currency_thresholds``. By default that is INR only (see
  ``DEFAULT_CURRENCY_THRESHOLDS``), with INR-denominated values.

INR thresholds are set in rupees and are never derived from, or compared
against, USD limits. INR uses an *inclusive* maximum: any INR payment at or
above the INR maximum is always BLOCK.

A supported currency without thresholds always requires REVIEW, because its
amount cannot be risk-assessed. An unsupported currency always requires
REVIEW as well.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from app.schemas.enums import PolicyDecision, RiskLevel
from app.services.risk.decision import DECISION_SEVERITY

LEVEL_ORDER: tuple[RiskLevel, ...] = (
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.CRITICAL,
)


def _default_level_decisions() -> Mapping[RiskLevel, PolicyDecision]:
    return MappingProxyType(
        {
            RiskLevel.LOW: PolicyDecision.ALLOW,
            RiskLevel.MEDIUM: PolicyDecision.REVIEW,
            RiskLevel.HIGH: PolicyDecision.REVIEW,
            RiskLevel.CRITICAL: PolicyDecision.BLOCK,
        }
    )


DEFAULT_SUPPORTED_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD", "INR"})
DEFAULT_CURRENCY = "USD"


def _is_currency_code(value: object) -> bool:
    return isinstance(value, str) and len(value) == 3 and value.isascii() and value.isalpha() and value.isupper()


def _is_finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


@dataclass(frozen=True)
class AmountThresholds:
    """Amount thresholds for one currency, in that currency's major units.

    Must satisfy ``0 < elevated < review < maximum``. Values must be finite
    ``Decimal`` instances; floats are rejected to avoid precision errors.

    ``block_at_maximum`` makes the maximum inclusive: amounts *at or above*
    ``maximum`` are blocked. When ``False`` (the default, and the USD
    behaviour) only amounts strictly above ``maximum`` are blocked.
    """

    elevated: Decimal
    review: Decimal
    maximum: Decimal
    block_at_maximum: bool = False

    def __post_init__(self) -> None:
        values = (self.elevated, self.review, self.maximum)
        if not all(_is_finite_decimal(v) for v in values):
            raise ValueError("amounts must be finite Decimal values (floats are not allowed)")
        if not Decimal("0") < self.elevated < self.review < self.maximum:
            raise ValueError("amounts must satisfy 0 < elevated < review < max")
        if not isinstance(self.block_at_maximum, bool):
            raise ValueError("block_at_maximum must be a bool")

    def exceeds_maximum(self, amount: Decimal) -> bool:
        """Whether ``amount`` is over the maximum (inclusive if ``block_at_maximum``)."""
        return amount >= self.maximum if self.block_at_maximum else amount > self.maximum

    def near_threshold_floor(self, ratio: Decimal) -> Decimal:
        """Lower bound of the 'just below review' band for the given ratio."""
        return self.review * (Decimal("1") - ratio)

# INR thresholds in rupees (major units), chosen as INR policy values. They are
# NOT converted from USD. INR payments at or above Rs 7,50,000 are always BLOCK.
INR_THRESHOLDS = AmountThresholds(
    elevated=Decimal("40000.00"),
    review=Decimal("150000.00"),
    maximum=Decimal("750000.00"),
    block_at_maximum=True,
)

DEFAULT_CURRENCY_THRESHOLDS: Mapping[str, AmountThresholds] = MappingProxyType(
    {"INR": INR_THRESHOLDS}
)

DEFAULT_PLACEHOLDERS = frozenset(
    {
        "n a", "na", "none", "null", "nil", "test", "testing", "tbd", "tbc",
        "todo", "misc", "unknown", "other", "stuff", "payment", "xxx", "asdf",
    }
)

DEFAULT_MERCHANT_PLACEHOLDERS = DEFAULT_PLACEHOLDERS | frozenset(
    {"merchant", "vendor", "payee", "seller", "company", "someone"}
)

DEFAULT_HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "crypto", "cryptocurrency", "bitcoin", "btc", "ethereum", "usdt",
    "gift card", "gift cards", "prepaid card", "wire transfer",
    "western union", "moneygram", "casino", "gambling", "betting", "lottery",
)

DEFAULT_URGENCY_KEYWORDS: tuple[str, ...] = (
    "urgent", "urgently", "immediately", "asap", "act now", "right away",
    "final notice", "within 24 hours", "last chance",
)

DEFAULT_OVERRIDE_PHRASES: tuple[str, ...] = (
    "ignore previous instructions", "ignore all previous instructions",
    "ignore prior instructions", "ignore the rules", "ignore policy",
    "bypass", "skip review", "skip the review", "skip approval",
    "override policy", "override the policy", "override the decision",
    "auto approve", "approve automatically", "already approved",
    "without approval", "without review", "no approval needed", "no review needed",
)


@dataclass(frozen=True)
class RiskPolicy:
    """Tunable parameters for the deterministic risk engine."""

    # --- Score bands (minimum score for each level; LOW starts at 0)
    medium_threshold: int = 25
    high_threshold: int = 50
    critical_threshold: int = 75

    # --- Level -> decision mapping
    level_decisions: Mapping[RiskLevel, PolicyDecision] = field(
        default_factory=_default_level_decisions
    )

    # --- Currency
    supported_currencies: frozenset[str] = DEFAULT_SUPPORTED_CURRENCIES
    unsupported_currency_points: int = 40  # minimum decision: REVIEW

    # Supported currencies other than default_currency with no entry in
    # currency_thresholds trigger CURRENCY_THRESHOLDS_NOT_CONFIGURED.
    unconfigured_currency_points: int = 25  # minimum decision: REVIEW

    # --- Amount thresholds for default_currency (major units). No FX
    # conversion is performed; these values apply ONLY to default_currency.
    default_currency: str = DEFAULT_CURRENCY
    elevated_amount: Decimal = Decimal("500.00")
    elevated_amount_points: int = 15
    review_amount: Decimal = Decimal("2000.00")
    review_amount_points: int = 35  # minimum decision: REVIEW
    max_amount: Decimal = Decimal("10000.00")
    over_limit_points: int = 60  # minimum decision: BLOCK
    # Amounts in [review_amount * (1 - ratio), review_amount) are flagged as
    # possibly sized to stay under the review threshold.
    near_threshold_ratio: Decimal = Decimal("0.10")
    near_threshold_points: int = 15
    # Explicit thresholds for other supported currencies, e.g.
    # {"EUR": AmountThresholds(Decimal("450"), Decimal("1800"), Decimal("9000"))}.
    # None (default) = DEFAULT_CURRENCY_THRESHOLDS for those currencies that are
    # in supported_currencies and are not default_currency. An explicit mapping
    # replaces the defaults entirely. Stored as a read-only copy.
    currency_thresholds: Mapping[str, AmountThresholds] | None = None

    # --- Merchant
    # A configured deny list. This is NOT a merchant verification source:
    # absence from this list says nothing about a merchant's legitimacy.
    blocked_merchants: frozenset[str] = frozenset()
    blocked_merchant_points: int = 100  # minimum decision: BLOCK
    merchant_placeholders: frozenset[str] = DEFAULT_MERCHANT_PLACEHOLDERS
    malformed_merchant_points: int = 25

    # --- Text content (merchant name, description, purpose, request text)
    high_risk_keywords: tuple[str, ...] = DEFAULT_HIGH_RISK_KEYWORDS
    high_risk_keyword_points: int = 30  # minimum decision: REVIEW
    urgency_keywords: tuple[str, ...] = DEFAULT_URGENCY_KEYWORDS
    urgency_points: int = 15
    override_phrases: tuple[str, ...] = DEFAULT_OVERRIDE_PHRASES
    override_attempt_points: int = 60  # minimum decision: BLOCK

    # --- Missing / vague details (description and purpose)
    detail_placeholders: frozenset[str] = DEFAULT_PLACEHOLDERS
    min_detail_length: int = 3
    vague_details_points: int = 25

    def __post_init__(self) -> None:
        # Freeze the mapping so later mutation of the caller's dict has no effect.
        if self.currency_thresholds is None:
            thresholds = {
                code: t
                for code, t in DEFAULT_CURRENCY_THRESHOLDS.items()
                if code in self.supported_currencies and code != self.default_currency
            }
        else:
            thresholds = dict(self.currency_thresholds)
        object.__setattr__(self, "currency_thresholds", MappingProxyType(thresholds))
        self._validate()

    def thresholds_for(self, currency: str) -> AmountThresholds | None:
        """Amount thresholds for ``currency``, or ``None`` if none are configured."""
        if currency == self.default_currency:
            return AmountThresholds(self.elevated_amount, self.review_amount, self.max_amount)
        assert self.currency_thresholds is not None  # set in __post_init__
        return self.currency_thresholds.get(currency)

    def level_for_score(self, score: int) -> RiskLevel:
        if score >= self.critical_threshold:
            return RiskLevel.CRITICAL
        if score >= self.high_threshold:
            return RiskLevel.HIGH
        if score >= self.medium_threshold:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @property
    def near_threshold_floor(self) -> Decimal:
        """Near-threshold band floor for ``default_currency``."""
        return self.review_amount * (Decimal("1") - self.near_threshold_ratio)

    def _validate(self) -> None:
        errors: list[str] = []

        if not 0 < self.medium_threshold < self.high_threshold < self.critical_threshold <= 100:
            errors.append("thresholds must satisfy 0 < medium < high < critical <= 100")

        missing = set(LEVEL_ORDER) - set(self.level_decisions)
        if missing:
            errors.append(f"level_decisions missing levels: {sorted(missing)}")
        else:
            for level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                if self.level_decisions[level] == PolicyDecision.ALLOW:
                    errors.append(f"{level} risk must never map to ALLOW")
            severities = [DECISION_SEVERITY[self.level_decisions[lv]] for lv in LEVEL_ORDER]
            if severities != sorted(severities):
                errors.append("higher risk levels must not map to less strict decisions")

        try:
            AmountThresholds(self.elevated_amount, self.review_amount, self.max_amount)
        except ValueError as exc:
            errors.append(f"default currency {exc}")
        if not (
            _is_finite_decimal(self.near_threshold_ratio)
            and Decimal("0") <= self.near_threshold_ratio < Decimal("1")
        ):
            errors.append("near_threshold_ratio must be a Decimal in [0, 1)")

        for name, value in vars(self).items():
            if name.endswith("_points") and not 0 <= value <= 100:
                errors.append(f"{name} must be between 0 and 100")

        if not self.supported_currencies or not all(
            _is_currency_code(c) for c in self.supported_currencies
        ):
            errors.append("supported_currencies must be non-empty three-letter uppercase codes")

        if not _is_currency_code(self.default_currency):
            errors.append("default_currency must be a three-letter uppercase code")
        elif self.default_currency not in self.supported_currencies:
            errors.append("default_currency must be one of supported_currencies")

        for code, thresholds in self.currency_thresholds.items():
            if not _is_currency_code(code):
                errors.append(f"currency_thresholds key {code!r} must be a three-letter uppercase code")
            elif code == self.default_currency:
                errors.append(
                    f"currency_thresholds must not contain default_currency {code}; "
                    "use elevated_amount/review_amount/max_amount instead"
                )
            elif code not in self.supported_currencies:
                errors.append(f"currency_thresholds key {code} is not in supported_currencies")
            if not isinstance(thresholds, AmountThresholds):
                errors.append(f"currency_thresholds[{code!r}] must be an AmountThresholds instance")

        if self.min_detail_length < 1:
            errors.append("min_detail_length must be at least 1")

        if errors:
            raise ValueError("Invalid RiskPolicy: " + "; ".join(errors))
