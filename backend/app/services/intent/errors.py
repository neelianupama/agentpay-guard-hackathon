"""Errors raised by the intent extraction service.

Every error carries a fixed, client-safe ``code`` and ``message``. Raw
provider error text is never copied into these errors, so it cannot leak
into API responses.
"""

from __future__ import annotations


class IntentExtractionError(Exception):
    """Base class. ``status_code`` is the HTTP status the API should return."""

    status_code: int = 502
    code: str = "INTENT_EXTRACTION_FAILED"
    message: str = "The payment request could not be parsed. Please try again."

    def __init__(self) -> None:
        super().__init__(self.message)


class IntentServiceNotConfiguredError(IntentExtractionError):
    status_code = 503
    code = "INTENT_SERVICE_UNAVAILABLE"
    message = "Payment request parsing is not available right now."


class IntentProviderUnavailableError(IntentExtractionError):
    """Provider unreachable, misconfigured credentials, or a server-side failure."""

    status_code = 503
    code = "INTENT_SERVICE_UNAVAILABLE"
    message = "Payment request parsing is not available right now."


class IntentModelUnavailableError(IntentExtractionError):
    status_code = 503
    code = "INTENT_MODEL_UNAVAILABLE"
    message = "The payment parsing model is not available right now."


class IntentRateLimitedError(IntentExtractionError):
    status_code = 429
    code = "INTENT_RATE_LIMITED"
    message = "Too many payment parsing requests. Please wait and try again."


class IntentTimeoutError(IntentExtractionError):
    status_code = 504
    code = "INTENT_TIMEOUT"
    message = "Parsing the payment request took too long. Please try again."


class InvalidModelOutputError(IntentExtractionError):
    """The model's output was not valid structured data in the expected shape."""

    status_code = 502
    code = "INTENT_EXTRACTION_FAILED"
    message = "The payment request could not be parsed. Please try again."
