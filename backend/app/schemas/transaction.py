"""Transaction contract."""

from __future__ import annotations

from pydantic import Field

from app.schemas.common import (
    APIModel,
    CurrencyCode,
    MoneyAmount,
    ShortText,
    TransactionId,
    UtcDatetime,
)
from app.schemas.enums import RiskLevel, TransactionStatus


class Transaction(APIModel):
    """A payment tracked by AgentPay Guard, as returned by the API."""

    transaction_id: TransactionId
    merchant_name: ShortText = Field(description="Merchant or payee name.")
    amount: MoneyAmount
    currency: CurrencyCode
    status: TransactionStatus
    risk_level: RiskLevel | None = Field(
        default=None,
        description="Risk level from assessment; null if not yet assessed.",
    )
    created_at: UtcDatetime
