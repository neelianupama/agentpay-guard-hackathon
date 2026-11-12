"""Risk assessment API.

The request body is the existing ``TransactionDetails`` contract, which
forbids unknown fields. Clients therefore cannot submit a risk score, risk
level, policy decision or agent recommendation: such fields are rejected
with HTTP 422. Every assessment is computed server-side by the
deterministic risk engine using the server's ``RiskPolicy``.
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends

from app.api.routes.audit import get_audit_recorder
from app.schemas.payment import TransactionDetails
from app.schemas.risk import RiskAssessment
from app.services.audit import AuditRecorder, risk_assessed
from app.services.risk import RiskEngine

router = APIRouter(prefix="/risk", tags=["risk"])

_engine = RiskEngine()


def get_risk_engine() -> RiskEngine:
    """Server-configured risk engine (default ``RiskPolicy``).

    Exposed as a dependency so the policy is controlled by the server, not
    the request. Tests may override it via ``app.dependency_overrides``.
    """
    return _engine


_EXAMPLES = {
    "low_allow": {
        "summary": "LOW risk -> ALLOW",
        "value": {
            "merchant_name": "Acme Cloud",
            "amount": "49.99",
            "currency": "USD",
            "description": "Monthly hosting invoice",
            "purpose": "Infrastructure hosting",
        },
    },
    "medium_review": {
        "summary": "USD amount at review threshold -> REVIEW",
        "value": {
            "merchant_name": "Acme Cloud",
            "amount": "2500.00",
            "currency": "USD",
            "description": "Annual hosting plan",
            "purpose": "Infrastructure hosting",
        },
    },
    "non_usd_review": {
        "summary": "Non-USD currency without configured thresholds -> REVIEW",
        "value": {
            "merchant_name": "Acme Cloud",
            "amount": "49.99",
            "currency": "EUR",
            "description": "Monthly hosting invoice",
            "purpose": "Infrastructure hosting",
        },
    },
    "critical_block": {
        "summary": "CRITICAL risk -> BLOCK",
        "value": {
            "merchant_name": "Acme Cloud",
            "amount": "3000.00",
            "currency": "USD",
            "description": "Urgent bitcoin purchase",
            "purpose": "Infrastructure hosting",
        },
    },
}


@router.post(
    "/assess",
    response_model=RiskAssessment,
    operation_id="assess_risk",
    summary="Assess the risk of a proposed payment",
    description=(
        "Runs the deterministic, rule-based risk engine on the proposed payment and "
        "returns a risk score (0-100), risk level, policy decision and human-readable "
        "reasons.\n\n"
        "- The decision is computed entirely server-side. No LLM is involved.\n"
        "- The request cannot include a risk score, level, decision or agent "
        "recommendation; unknown fields are rejected with 422.\n"
        "- Amount thresholds apply only to USD and to currencies with explicitly "
        "configured thresholds. Any other currency always requires REVIEW.\n"
        "- This endpoint only assesses risk. It does not execute or store a payment."
    ),
    response_description="Risk assessment computed by the server.",
)
async def assess_risk(
    details: Annotated[TransactionDetails, Body(openapi_examples=_EXAMPLES)],
    engine: Annotated[RiskEngine, Depends(get_risk_engine)],
    audit: Annotated[AuditRecorder, Depends(get_audit_recorder)],
) -> RiskAssessment:
    assessment = engine.assess(details)
    try:
        audit.emit(risk_assessed(details, assessment))  # never blocks or fails the response
    except Exception:  # noqa: BLE001
        pass
    return assessment
