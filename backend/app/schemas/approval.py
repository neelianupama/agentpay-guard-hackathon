"""Human approval contract."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from app.schemas.common import APIModel, TransactionId
from app.schemas.enums import ApprovalDecision

ReviewerComment = Annotated[str, StringConstraints(max_length=1000)]


class ApprovalRequest(APIModel):
    """A human reviewer's decision on a transaction awaiting review."""

    transaction_id: TransactionId
    decision: ApprovalDecision
    comment: ReviewerComment | None = Field(
        default=None,
        description="Optional note explaining the decision.",
        examples=["Verified invoice with the finance team"],
    )
