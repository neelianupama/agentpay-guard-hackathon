"""Natural-language payment intent parsing contracts.

The request body is the existing ``PaymentIntentRequest`` (``request_text``).
The response, ``ParsedPaymentIntent``, carries only the payment details that
could be extracted *and* validated by the backend. Fields that were not
stated, were ambiguous, or failed validation are ``null`` and listed in
``missing_fields``.

This contract deliberately has no risk score, risk level, policy decision,
approval or execution fields: parsing an intent never assesses, approves or
executes a payment.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from app.schemas.common import APIModel, CurrencyCode, LongText, MoneyAmount, ShortText


class PaymentDetailField(StrEnum):
    """The payment detail fields an intent can be missing, in canonical order."""

    MERCHANT_NAME = "merchant_name"
    AMOUNT = "amount"
    CURRENCY = "currency"
    DESCRIPTION = "description"
    PURPOSE = "purpose"


class ParsedPaymentIntent(APIModel):
    """Payment details extracted from a natural-language request.

    Each detail uses the same constraints as ``TransactionDetails`` but may be
    ``null`` when it could not be determined. ``missing_fields`` always lists
    exactly the ``null`` details, and ``clarification_needed`` is ``true``
    whenever anything is missing (it may also be ``true`` when the request is
    ambiguous even though every field has a value).
    """

    merchant_name: ShortText | None = Field(
        default=None,
        description="Merchant or payee name, as written in the request.",
        examples=["ABC Technologies"],
    )
    amount: MoneyAmount | None = None
    currency: CurrencyCode | None = Field(
        default=None,
        description=(
            "ISO 4217 code stated in the request. Never defaulted and never "
            "converted from another currency."
        ),
        examples=["USD"],
    )
    description: LongText | None = Field(
        default=None,
        description="What the payment is for.",
        examples=["Software services"],
    )
    purpose: ShortText | None = Field(
        default=None,
        description="Business purpose stated by the requester.",
        examples=["Engineering tooling"],
    )
    missing_fields: list[PaymentDetailField] = Field(
        default_factory=list,
        description="Payment details that are null and must be supplied before risk assessment.",
        examples=[["purpose"]],
    )
    clarification_needed: bool = Field(
        description="True when the requester must clarify or supply details before proceeding.",
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> ParsedPaymentIntent:
        expected = [f for f in PaymentDetailField if getattr(self, f.value) is None]
        if self.missing_fields != expected:
            raise ValueError(
                "missing_fields must list exactly the null payment details, in canonical order"
            )
        if expected and not self.clarification_needed:
            raise ValueError("clarification_needed must be true when payment details are missing")
        return self
