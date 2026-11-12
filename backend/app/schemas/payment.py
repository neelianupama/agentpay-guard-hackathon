"""Payment intent contracts.

Flow: a client submits a natural-language ``PaymentIntentRequest``; it is
parsed by ``POST /api/v1/intent/parse`` into a ``ParsedPaymentIntent``
(see ``app.schemas.intent``).
"""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import APIModel, CurrencyCode, LongText, MoneyAmount, ShortText


class PaymentIntentRequest(APIModel):
    """Natural-language payment request submitted by a user or AI agent."""

    request_text: LongText = Field(
        description="Payment request in plain language.",
        examples=["Pay Acme Cloud $49.99 for the monthly hosting invoice"],
    )


class TransactionDetails(APIModel):
    """Structured details of a proposed payment.

    Shared by ``PaymentIntent`` and ``RiskAssessment`` so that the payment
    being assessed is described the same way everywhere.
    """

    merchant_name: ShortText = Field(
        description="Merchant or payee name.",
        examples=["Acme Cloud"],
    )
    amount: MoneyAmount
    currency: CurrencyCode
    description: LongText = Field(
        description="What the payment is for.",
        examples=["Monthly hosting invoice"],
    )
    purpose: ShortText = Field(
        description="Business purpose the requester gave for the payment.",
        examples=["Infrastructure hosting"],
    )


class PaymentIntent(TransactionDetails):
    """Structured payment intent parsed from a natural-language request."""

    request_text: LongText = Field(
        description="The original natural-language request this intent was parsed from.",
    )
