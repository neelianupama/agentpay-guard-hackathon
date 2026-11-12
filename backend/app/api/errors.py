"""Exception handlers for payment workflow and reviewer auth errors.

All errors are rendered as ``ErrorResponse`` with fixed, client-safe text.
Raw PayPal responses, tokens and credentials are never included.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.security import ReviewerAuthError
from app.schemas.error import ErrorResponse
from app.services.payments import PaymentWorkflowError

# Only these detail keys may be returned to clients.
_SAFE_DETAIL_KEYS = frozenset({"transaction_id", "status", "retry_safe", "paypal_order_status"})


def payment_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PaymentWorkflowError)
    details = {k: v for k, v in exc.details.items() if k in _SAFE_DETAIL_KEYS} or None
    body = ErrorResponse(code=exc.code, message=exc.message, details=details)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))


def reviewer_auth_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ReviewerAuthError)
    body = ErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content=body.model_dump(exclude_none=True),
        headers=exc.headers,
    )


def register_payment_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(PaymentWorkflowError, payment_error_handler)
    app.add_exception_handler(ReviewerAuthError, reviewer_auth_error_handler)
