"""Standard API error response contract."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, StringConstraints

from app.schemas.common import APIModel

ErrorCode = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$"),
]


class ErrorResponse(APIModel):
    """Consistent error body returned by every endpoint on failure."""

    code: ErrorCode = Field(
        description="Machine-readable error code in UPPER_SNAKE_CASE.",
        examples=["VALIDATION_ERROR"],
    )
    message: str = Field(
        min_length=1,
        description="Human-readable explanation of the error.",
        examples=["Currency must be a three-letter uppercase code."],
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured context, such as field-level errors.",
    )
