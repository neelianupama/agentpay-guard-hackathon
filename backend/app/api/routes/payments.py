"""Payment workflow API: risk-gated PayPal Orders v2 (Sandbox), human-approved capture.

Endpoints (prefix ``/api/v1/payments``):

- ``POST   /``                         create: risk assessment -> BLOCKED / PENDING_REVIEW / PayPal order
- ``GET    /``                         list (newest first, paginated)
- ``GET    /{transaction_id}``          read one
- ``POST   /{transaction_id}/order``    retry PayPal order creation (APPROVED_FOR_PAYMENT only)
- ``POST   /{transaction_id}/authorize`` verify buyer approval with PayPal, then authorize
- ``POST   /{transaction_id}/approve``  reviewer: approve a PENDING_REVIEW payment
- ``POST   /{transaction_id}/reject``   reviewer: reject a PENDING_REVIEW payment
- ``POST   /{transaction_id}/capture``  reviewer: capture an AUTHORIZED payment
- ``POST   /{transaction_id}/void``     reviewer: void an AUTHORIZED payment

STORAGE IS IN-MEMORY: records are lost on restart; run a single worker.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status

from app.api.routes.audit import get_audit_recorder
from app.api.routes.risk import get_risk_engine
from app.api.security import ReviewerRequired
from app.core.config import Settings, get_settings
from app.schemas.enums import PaymentStatus
from app.schemas.error import ErrorResponse
from app.schemas.payment import TransactionDetails
from app.schemas.payments import (
    PaymentActionError,
    PaymentActionResponse,
    PaymentErrorSummary,
    PaymentListResponse,
    PaymentRecordResponse,
    PaymentRiskSummary,
    PayPalSummary,
    ReviewerActionLog,
    ReviewerActionRequest,
    ReviewSummary,
)
from app.services.audit import AuditRecorder
from app.services.payments import InMemoryPaymentRepository, PaymentRecord, PaymentWorkflow
from app.services.payments.records import NEXT_ACTIONS
from app.services.payments.workflow import OrdersFactory
from app.services.paypal import PayPalClient, PayPalOrdersService
from app.services.risk import RiskEngine

router = APIRouter(prefix="/payments", tags=["payments"])

_repository = InMemoryPaymentRepository()


def get_payment_repository() -> InMemoryPaymentRepository:
    return _repository


@lru_cache(maxsize=4)
def _orders_service(settings: Settings) -> PayPalOrdersService:
    return PayPalOrdersService(PayPalClient.from_settings(settings))


def get_orders_factory(settings: Annotated[Settings, Depends(get_settings)]) -> OrdersFactory:
    """Lazily builds the PayPal service, so BLOCK/REVIEW work without PayPal configured."""
    return lambda: _orders_service(settings)


def get_payment_workflow(
    repository: Annotated[InMemoryPaymentRepository, Depends(get_payment_repository)],
    engine: Annotated[RiskEngine, Depends(get_risk_engine)],
    orders_factory: Annotated[OrdersFactory, Depends(get_orders_factory)],
    audit: Annotated[AuditRecorder, Depends(get_audit_recorder)],
) -> PaymentWorkflow:
    return PaymentWorkflow(repository, engine, orders_factory, audit=audit)


Workflow = Annotated[PaymentWorkflow, Depends(get_payment_workflow)]
TransactionIdPath = Annotated[
    str,
    Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$",
         description="Internal transaction ID (e.g. txn_...)."),
]
ReviewerBody = Annotated[ReviewerActionRequest | None, Body()]

_ERRORS: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Reviewer authentication missing or invalid."},
    404: {"model": ErrorResponse, "description": "Payment not found."},
    409: {"model": ErrorResponse, "description": "Not allowed in the current status, or busy."},
    422: {"model": ErrorResponse, "description": "Invalid input, or currency not allowed at PayPal."},
    429: {"model": ErrorResponse, "description": "PayPal rate limit."},
    502: {"model": ErrorResponse, "description": "Unexpected PayPal response (retry is safe)."},
    503: {"model": ErrorResponse, "description": "PayPal or reviewer auth unavailable/not configured."},
    504: {"model": ErrorResponse, "description": "PayPal timeout (retry is safe; same request ID)."},
}


def to_response(record: PaymentRecord) -> PaymentRecordResponse:
    return PaymentRecordResponse(
        transaction_id=record.transaction_id,
        status=record.status,
        merchant_name=record.merchant_name,
        amount=record.amount,
        currency=record.currency,
        description=record.description,
        purpose=record.purpose,
        risk=PaymentRiskSummary(
            risk_score=record.risk_score,
            risk_level=record.risk_level,
            decision=record.decision,
            reasons=list(record.reasons),
            assessed_at=record.assessed_at,
        ),
        paypal=PayPalSummary(
            order_id=record.paypal_order_id,
            approval_url=record.paypal_approval_url,
            order_status=record.paypal_order_status,
            authorization_id=record.paypal_authorization_id,
            authorization_status=record.paypal_authorization_status,
            capture_id=record.paypal_capture_id,
            capture_status=record.paypal_capture_status,
        ),
        review=(
            ReviewSummary(
                decision=record.review.decision,
                reviewer_name=record.review.reviewer_name,
                comment=record.review.comment,
                decided_at=record.review.decided_at,
            )
            if record.review
            else None
        ),
        reviewer_actions=[
            ReviewerActionLog(action=a.action, reviewer_name=a.reviewer_name,
                              comment=a.comment, at=a.at)
            for a in record.reviewer_actions
        ],
        last_error=(
            PaymentErrorSummary(code=record.last_error.code, message=record.last_error.message,
                                retry_safe=record.last_error.retry_safe, at=record.last_error.at)
            if record.last_error
            else None
        ),
        next_actions=list(NEXT_ACTIONS.get(record.status, ())),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


# Actions a buyer/agent may take without reviewer authentication.
CLIENT_NEXT_ACTIONS = ("create_order", "buyer_approves_on_paypal", "authorize")


def to_action_response(record: PaymentRecord) -> PaymentActionResponse:
    """Minimal, explicitly allowlisted view for UNAUTHENTICATED action endpoints."""
    return PaymentActionResponse(
        transaction_id=record.transaction_id,
        status=record.status,
        merchant_name=record.merchant_name,
        amount=record.amount,
        currency=record.currency,
        approval_url=(
            record.paypal_approval_url
            if record.status is PaymentStatus.AWAITING_BUYER_APPROVAL
            else None
        ),
        next_actions=[a for a in NEXT_ACTIONS.get(record.status, ()) if a in CLIENT_NEXT_ACTIONS],
        last_error=(
            PaymentActionError(code=record.last_error.code, retry_safe=record.last_error.retry_safe)
            if record.last_error
            else None
        ),
    )


def _reviewer(body: ReviewerActionRequest | None) -> dict[str, str | None]:
    body = body or ReviewerActionRequest()
    return {"reviewer_name": body.reviewer_name, "comment": body.comment}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=PaymentActionResponse,
    operation_id="create_payment",
    summary="Create a payment (risk-gated)",
    description=(
        "Runs the backend risk engine on the payment details (client-supplied scores or "
        "decisions are rejected with 422).\n\n"
        "- BLOCK: stored as BLOCKED; nothing is sent to PayPal.\n"
        "- REVIEW: stored as PENDING_REVIEW; nothing is sent to PayPal until a reviewer approves.\n"
        "- ALLOW: an AUTHORIZE-intent PayPal Sandbox order is created; the buyer must approve it "
        "at `paypal.approval_url`. Funds are never captured here.\n\n"
        "Currencies PayPal may not receive (INR) are rejected with 422 before any PayPal call; "
        "nothing is converted. If PayPal fails ambiguously the payment is kept "
        "(APPROVED_FOR_PAYMENT) and `POST /{transaction_id}/order` retries safely.\n\n"
        "No reviewer token is needed, so the response is a **minimal** view (status, amount, "
        "approval link, allowed buyer actions). The full record, including risk details, is "
        "available to reviewers via `GET /{transaction_id}`."
    ),
    responses=_ERRORS,
)
async def create_payment(details: TransactionDetails, workflow: Workflow) -> PaymentActionResponse:
    return to_action_response(await workflow.create_payment(details))


@router.get("", response_model=PaymentListResponse, operation_id="list_payments",
            summary="Reviewer: list payments (newest first)",
            description="Requires `Authorization: Bearer <REVIEWER_TOKEN>` (records include "
                        "reviewer names and comments).",
            responses=_ERRORS)
async def list_payments(
    _: ReviewerRequired,
    workflow: Workflow,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> PaymentListResponse:
    records, total = workflow.list(limit=limit, offset=offset)
    return PaymentListResponse(items=[to_response(r) for r in records], total=total,
                               limit=limit, offset=offset)


@router.get("/{transaction_id}", response_model=PaymentRecordResponse,
            operation_id="get_payment", summary="Reviewer: get one payment",
            description="Requires `Authorization: Bearer <REVIEWER_TOKEN>`.",
            responses=_ERRORS)
async def get_payment(
    transaction_id: TransactionIdPath, _: ReviewerRequired, workflow: Workflow
) -> PaymentRecordResponse:
    return to_response(workflow.get(transaction_id))


@router.post("/{transaction_id}/order", response_model=PaymentActionResponse,
             operation_id="create_payment_order",
             summary="Create or safely retry the PayPal order (APPROVED_FOR_PAYMENT only)",
             description="No reviewer token needed; returns the minimal action view.",
             responses=_ERRORS)
async def create_order(transaction_id: TransactionIdPath, workflow: Workflow) -> PaymentActionResponse:
    return to_action_response(await workflow.create_order(transaction_id))


@router.post(
    "/{transaction_id}/authorize",
    response_model=PaymentActionResponse,
    operation_id="authorize_payment",
    summary="Verify buyer approval with PayPal and authorize (never captures)",
    description=(
        "Takes no body: buyer approval is read from PayPal, never from the client. "
        "The order is authorized only if PayPal reports it APPROVED. An authorization PayPal "
        "reports as PENDING stays AUTHORIZATION_PENDING (call again to refresh). "
        "No reviewer token needed; returns the minimal action view."
    ),
    responses=_ERRORS,
)
async def authorize_payment(transaction_id: TransactionIdPath, workflow: Workflow) -> PaymentActionResponse:
    return to_action_response(await workflow.authorize(transaction_id))


@router.post("/{transaction_id}/approve", response_model=PaymentRecordResponse,
             operation_id="approve_payment",
             summary="Reviewer: approve a PENDING_REVIEW payment (creates the PayPal order)",
             responses=_ERRORS)
async def approve_payment(
    transaction_id: TransactionIdPath, _: ReviewerRequired, workflow: Workflow,
    body: ReviewerBody = None,
) -> PaymentRecordResponse:
    return to_response(await workflow.approve(transaction_id, **_reviewer(body)))


@router.post("/{transaction_id}/reject", response_model=PaymentRecordResponse,
             operation_id="reject_payment", summary="Reviewer: reject a PENDING_REVIEW payment",
             responses=_ERRORS)
async def reject_payment(
    transaction_id: TransactionIdPath, _: ReviewerRequired, workflow: Workflow,
    body: ReviewerBody = None,
) -> PaymentRecordResponse:
    return to_response(await workflow.reject(transaction_id, **_reviewer(body)))


@router.post(
    "/{transaction_id}/capture",
    response_model=PaymentRecordResponse,
    operation_id="capture_payment",
    summary="Reviewer: capture an AUTHORIZED payment",
    description=(
        "Explicit human capture of the stored authorization (exact amount and currency). "
        "COMPLETED -> CAPTURED; PENDING -> CAPTURE_PENDING. After an ambiguous failure the "
        "payment stays PENDING_CAPTURE and calling this again reuses the same PayPal request ID, "
        "so funds can never be captured twice."
    ),
    responses=_ERRORS,
)
async def capture_payment(
    transaction_id: TransactionIdPath, _: ReviewerRequired, workflow: Workflow,
    body: ReviewerBody = None,
) -> PaymentRecordResponse:
    return to_response(await workflow.capture(transaction_id, **_reviewer(body)))


@router.post("/{transaction_id}/void", response_model=PaymentRecordResponse,
             operation_id="void_payment",
             summary="Reviewer: void an AUTHORIZED payment (not after capture)",
             responses=_ERRORS)
async def void_payment(
    transaction_id: TransactionIdPath, _: ReviewerRequired, workflow: Workflow,
    body: ReviewerBody = None,
) -> PaymentRecordResponse:
    return to_response(await workflow.void(transaction_id, **_reviewer(body)))
