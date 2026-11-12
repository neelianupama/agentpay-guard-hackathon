"""Shared building blocks for API schemas.

Reusable annotated types and the base model that every AgentPay Guard
contract inherits from. Keeping these in one place guarantees that money,
currency codes, identifiers and timestamps are validated identically
across every endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)


class APIModel(BaseModel):
    """Base model for all API contracts.

    - ``extra="forbid"``: unknown fields are rejected rather than silently dropped.
    - ``str_strip_whitespace``: leading/trailing whitespace is removed from strings.
    - ``validate_assignment``: constraints are re-checked when fields are mutated.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


MoneyAmount = Annotated[
    Decimal,
    Field(
        gt=Decimal("0"),
        max_digits=14,
        decimal_places=2,
        description=(
            "Positive monetary amount with at most 2 decimal places. "
            "Serialized as a string to avoid floating-point precision loss."
        ),
        examples=["49.99"],
    ),
]
"""Strictly positive Decimal amount. Floats are never used for money."""

CurrencyCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{3}$"),
    Field(
        description="ISO 4217 currency code: exactly three uppercase letters.",
        examples=["USD"],
    ),
]
"""Three uppercase letters, e.g. ``USD``. Lowercase input is rejected."""

TransactionId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    Field(
        description="Opaque transaction identifier assigned by AgentPay Guard.",
        examples=["txn_01HZX3K9Q4"],
    ),
]
"""Opaque identifier: 1-64 characters of letters, digits, ``_`` or ``-``."""

UtcDatetime = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc),
    Field(description="Timezone-aware timestamp, normalized to UTC (ISO 8601)."),
]
"""Timezone-aware datetime normalized to UTC. Naive datetimes are rejected."""

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
