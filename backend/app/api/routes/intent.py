"""Natural-language payment intent parsing API.

``POST /api/v1/intent/parse`` turns a plain-language payment request into
structured, backend-validated payment details. It only extracts: it does not
call the risk engine, does not return a risk score or decision, and does not
approve, store or execute a payment. Clients must still submit the details
to ``POST /api/v1/risk/assess`` (after supplying anything missing).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated

import groq
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.schemas.error import ErrorResponse
from app.schemas.intent import ParsedPaymentIntent
from app.schemas.payment import PaymentIntentRequest
from app.services.intent import (
    IntentExtractionError,
    IntentExtractor,
    IntentServiceNotConfiguredError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intent", tags=["intent"])

# The SDK retries rate limits, connection errors and 5xx responses itself.
# Keep it to one retry so a request fails fast within the timeout budget.
GROQ_MAX_RETRIES = 1


@lru_cache(maxsize=4)
def _build_extractor(api_key: str, model: str, timeout_seconds: float) -> IntentExtractor:
    client = groq.Groq(api_key=api_key, timeout=timeout_seconds, max_retries=GROQ_MAX_RETRIES)
    return IntentExtractor(client=client, model=model)


def get_intent_extractor(
    settings: Annotated[Settings, Depends(get_settings)],
) -> IntentExtractor:
    """Server-configured extractor. Tests override this dependency with a fake client."""
    if not settings.groq_api_key:
        logger.warning("Intent extraction requested but GROQ_API_KEY is not configured")
        raise IntentServiceNotConfiguredError()
    return _build_extractor(
        settings.groq_api_key, settings.groq_model, settings.groq_timeout_seconds
    )


def intent_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render extraction errors as ``ErrorResponse`` with fixed, client-safe text."""
    assert isinstance(exc, IntentExtractionError)
    body = ErrorResponse(code=exc.code, message=exc.message)
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(exclude_none=True))


_EXAMPLES = {
    "complete": {
        "summary": "All details stated",
        "value": {
            "request_text": (
                "Pay 250 USD to ABC Technologies for software services "
                "for the engineering team's tooling budget."
            )
        },
    },
    "incomplete": {
        "summary": "Missing amount and currency",
        "value": {"request_text": "Pay ABC Technologies for software services."},
    },
}

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    429: {"model": ErrorResponse, "description": "Provider rate limit reached. Retry later."},
    502: {"model": ErrorResponse, "description": "The model output could not be used."},
    503: {"model": ErrorResponse, "description": "Parsing service or model unavailable."},
    504: {"model": ErrorResponse, "description": "The provider did not respond in time."},
}


@router.post(
    "/parse",
    response_model=ParsedPaymentIntent,
    operation_id="parse_payment_intent",
    summary="Extract payment details from a natural-language request",
    description=(
        "Uses an LLM to extract merchant, amount, currency, description and purpose from a "
        "plain-language payment request. Every value is re-validated by the backend.\n\n"
        "- Only explicitly stated details are returned; nothing is invented or defaulted. "
        "Missing, ambiguous or unverifiable details are `null` and listed in `missing_fields`.\n"
        "- Merchant, amount and currency must appear in the request text, otherwise they are "
        "discarded.\n"
        "- Currencies are never converted. Any currency (e.g. INR) is returned as stated and "
        "must still pass the normal validation in `POST /api/v1/risk/assess`.\n"
        "- The request text is treated as untrusted data; instructions inside it are ignored.\n"
        "- This endpoint does **not** assess risk, decide ALLOW/REVIEW/BLOCK, approve, store "
        "or execute a payment."
    ),
    response_description="Extracted, backend-validated payment details.",
    responses=_ERROR_RESPONSES,
)
def parse_payment_intent(
    payload: Annotated[PaymentIntentRequest, Body(openapi_examples=_EXAMPLES)],
    extractor: Annotated[IntentExtractor, Depends(get_intent_extractor)],
) -> ParsedPaymentIntent:
    return extractor.extract(payload.request_text)
