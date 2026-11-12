"""Deterministic risk assessment engine.

Usage::

    from app.services.risk import RiskEngine

    assessment = RiskEngine().assess(transaction_details)

See ``policy.py`` for thresholds and decision mapping, and ``rules.py`` for
the rule catalogue.
"""

from app.services.risk.decision import resolve_final_decision, strictest
from app.services.risk.engine import RiskEngine, assess_transaction
from app.services.risk.policy import AmountThresholds, RiskPolicy
from app.services.risk.rules import DEFAULT_RULES, RuleHit

__all__ = [
    "DEFAULT_RULES",
    "AmountThresholds",
    "RiskEngine",
    "RiskPolicy",
    "RuleHit",
    "assess_transaction",
    "resolve_final_decision",
    "strictest",
]
