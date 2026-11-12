"""Backend checks that extracted values are grounded in the request text.

Model output is never trusted. Besides schema validation, the merchant name,
amount and currency must be traceable to the original request text; values
that are not (for example hallucinated or injected values) are discarded and
reported as missing.

These checks are intentionally conservative: when grounding is uncertain the
value is dropped and the user is asked to clarify, never guessed.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_WS_RE = re.compile(r"\s+")
_NUMBER_RE = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")

# Patterns (matched against casefolded text) for names or symbols that
# identify exactly one currency. Ambiguous forms such as "$", "dollars",
# "rupees" or "¥" are deliberately absent.
_CURRENCY_ALIASES: dict[str, tuple[str, ...]] = {
    "USD": (r"(?<![a-z])us\$", r"\bu\.?s\.? dollars?\b", r"\bunited states dollars?\b"),
    "EUR": (r"€", r"\beuros?\b"),
    "GBP": (r"£", r"\bbritish pounds?\b", r"\bpounds? sterling\b"),
    "CAD": (r"(?<![a-z])(?:c|ca|cad)\$", r"\bcanadian dollars?\b"),
    "AUD": (r"(?<![a-z])(?:a|au|aud)\$", r"\baustralian dollars?\b"),
    "INR": (r"₹", r"\bindian rupees?\b"),
    "JPY": (r"\bjapanese yen\b",),
}

# Codes that may also be matched in lowercase ("pay 20 usd"). Other codes must
# be uppercase, because many are ordinary words in lowercase ("try", "all").
_CASE_INSENSITIVE_CODES = frozenset(_CURRENCY_ALIASES)


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", text).strip().casefold()


def merchant_is_grounded(merchant_name: str, request_text: str) -> bool:
    """The merchant name must appear in the request (case/whitespace-insensitive)."""
    needle = _normalize(merchant_name)
    return bool(needle) and needle in _normalize(request_text)


def amounts_in_text(request_text: str) -> set[Decimal]:
    """Numeric values written in digits in the request, ignoring thousands separators."""
    values: set[Decimal] = set()
    for match in _NUMBER_RE.findall(request_text):
        try:
            values.add(Decimal(match.replace(",", "")))
        except InvalidOperation:
            continue
    return values


def amount_is_grounded(amount: Decimal, request_text: str) -> bool:
    """The amount must equal a number written in digits in the request."""
    return amount in amounts_in_text(request_text)


def currency_is_grounded(currency: str, request_text: str) -> bool:
    """The currency code, or an unambiguous name/symbol for it, must appear in the request."""
    flags = re.IGNORECASE if currency in _CASE_INSENSITIVE_CODES else 0
    if re.search(rf"(?<![A-Za-z]){re.escape(currency)}(?![A-Za-z])", request_text, flags):
        return True
    text = _normalize(request_text)
    return any(re.search(pattern, text) for pattern in _CURRENCY_ALIASES.get(currency, ()))
