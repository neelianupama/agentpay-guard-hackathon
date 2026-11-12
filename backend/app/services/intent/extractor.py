"""Payment intent extraction with a Groq-hosted LLM.

Pipeline for one request:

1. Send the fixed system prompt and the fenced, untrusted request text to the
   model, asking for strict structured (JSON-schema) output.
2. Reject output that is not a JSON object of exactly the expected shape.
3. Validate each value with the same constraints as ``TransactionDetails``
   and check merchant, amount and currency against the request text.
   Invalid or ungrounded values are dropped and reported as missing.
4. Build a ``ParsedPaymentIntent``, which re-checks consistency.

The extractor only extracts. It never calls the risk engine, never produces a
risk score or policy decision, and never approves or executes a payment.
Provider errors are mapped to client-safe ``IntentExtractionError`` subclasses
and logged by class name and status only: never the API key, never raw
provider messages, never request text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

import groq
from pydantic import TypeAdapter, ValidationError

from app.schemas.common import CurrencyCode, LongText, MoneyAmount, ShortText
from app.schemas.intent import ParsedPaymentIntent
from app.services.intent.errors import (
    IntentExtractionError,
    IntentModelUnavailableError,
    IntentProviderUnavailableError,
    IntentRateLimitedError,
    IntentTimeoutError,
    InvalidModelOutputError,
)
from app.services.intent.grounding import (
    amount_is_grounded,
    currency_is_grounded,
    merchant_is_grounded,
)
from app.services.intent.prompts import EXTRACTION_FIELDS, RESPONSE_FORMAT, build_messages

logger = logging.getLogger(__name__)

MAX_COMPLETION_TOKENS = 2048

_FIELD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "merchant_name": TypeAdapter(ShortText),
    "amount": TypeAdapter(MoneyAmount),
    "currency": TypeAdapter(CurrencyCode),
    "description": TypeAdapter(LongText),
    "purpose": TypeAdapter(ShortText),
}

_GROUNDING_CHECKS: dict[str, Callable[[Any, str], bool]] = {
    "merchant_name": merchant_is_grounded,
    "amount": amount_is_grounded,
    "currency": currency_is_grounded,
}

_EXPECTED_KEYS = frozenset((*EXTRACTION_FIELDS, "clarification_needed"))


class ChatClient(Protocol):
    """The part of the Groq client the extractor uses (lets tests inject a fake)."""

    @property
    def chat(self) -> Any: ...


class IntentExtractor:
    def __init__(self, client: ChatClient, model: str) -> None:
        self._client = client
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def extract(self, request_text: str) -> ParsedPaymentIntent:
        content = self._complete(request_text)
        raw = _parse_output(content)
        return _validate_output(raw, request_text)

    def _complete(self, request_text: str) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=build_messages(request_text),
                response_format=RESPONSE_FORMAT,
                temperature=0,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                reasoning_effort="low",
                include_reasoning=False,
            )
        except groq.APIError as exc:
            raise _map_provider_error(exc) from None

        try:
            choice = completion.choices[0]
            content = choice.message.content
            finish_reason = choice.finish_reason
        except (AttributeError, IndexError, TypeError):
            logger.warning("Intent extraction: provider returned no usable choice")
            raise InvalidModelOutputError() from None

        if finish_reason not in (None, "stop") or not isinstance(content, str):
            logger.warning(
                "Intent extraction: incomplete model output (finish_reason=%s)", finish_reason
            )
            raise InvalidModelOutputError()
        return content


def _map_provider_error(exc: groq.APIError) -> IntentExtractionError:
    """Map an SDK error to a client-safe error. Order matters: subclasses first."""
    status = getattr(exc, "status_code", None)
    logger.warning(
        "Intent extraction provider error: %s (status=%s)", type(exc).__name__, status
    )
    if isinstance(exc, groq.APITimeoutError):
        return IntentTimeoutError()
    if isinstance(exc, groq.APIConnectionError):
        return IntentProviderUnavailableError()
    if isinstance(exc, groq.RateLimitError):
        return IntentRateLimitedError()
    if isinstance(exc, groq.NotFoundError):
        return IntentModelUnavailableError()
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)):
        return IntentProviderUnavailableError()
    if isinstance(exc, groq.APIStatusError) and exc.status_code >= 500:
        return IntentProviderUnavailableError()
    # 400/422 (including the provider rejecting output that broke the JSON
    # schema) and anything unexpected.
    return InvalidModelOutputError()


def _parse_output(content: str) -> dict[str, Any]:
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Intent extraction: model output is not valid JSON")
        raise InvalidModelOutputError() from None

    if not isinstance(raw, dict) or set(raw) != _EXPECTED_KEYS:
        logger.warning("Intent extraction: model output has an unexpected shape")
        raise InvalidModelOutputError()
    if not isinstance(raw["clarification_needed"], bool):
        logger.warning("Intent extraction: clarification_needed is not a boolean")
        raise InvalidModelOutputError()
    for name in EXTRACTION_FIELDS:
        if raw[name] is not None and not isinstance(raw[name], str):
            logger.warning("Intent extraction: field %s has an unexpected type", name)
            raise InvalidModelOutputError()
    return raw


def _validate_output(raw: dict[str, Any], request_text: str) -> ParsedPaymentIntent:
    values: dict[str, Any] = {}
    rejected = False
    for name in EXTRACTION_FIELDS:
        value = raw[name].strip() if isinstance(raw[name], str) else None
        if not value:
            values[name] = None
            continue
        try:
            parsed = _FIELD_ADAPTERS[name].validate_python(value)
        except ValidationError:
            logger.info("Intent extraction: dropped %s (failed validation)", name)
            values[name], rejected = None, True
            continue
        grounded = _GROUNDING_CHECKS.get(name)
        if grounded is not None and not grounded(parsed, request_text):
            logger.info("Intent extraction: dropped %s (not found in request)", name)
            values[name], rejected = None, True
            continue
        values[name] = parsed

    missing = [name for name in EXTRACTION_FIELDS if values[name] is None]
    return ParsedPaymentIntent(
        **values,
        missing_fields=missing,
        clarification_needed=bool(raw["clarification_needed"] or missing or rejected),
    )
