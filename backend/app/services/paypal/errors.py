"""Errors raised by the PayPal client.

Every error carries a fixed, client-safe ``code`` and ``message`` plus the
HTTP status the API should return. Credentials, access tokens,
authorization headers and raw PayPal response bodies are never included,
and the original transport exception is never chained (``from None``), so
nothing sensitive can surface through tracebacks or error responses.
"""

from __future__ import annotations


class PayPalError(Exception):
    """Base class for PayPal failures."""

    status_code: int = 502
    code: str = "PAYPAL_ERROR"
    message: str = "The payment provider returned an unexpected error."

    def __init__(self, *, upstream_status: int | None = None) -> None:
        super().__init__(self.message)
        # PayPal's HTTP status (not sensitive); useful for diagnostics and tests.
        self.upstream_status = upstream_status

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, upstream_status={self.upstream_status!r})"


class PayPalNotConfiguredError(PayPalError):
    status_code = 503
    code = "PAYPAL_NOT_CONFIGURED"
    message = "PayPal is not configured on the server."


class PayPalAuthenticationError(PayPalError):
    """PayPal rejected the server's client credentials (HTTP 401/403)."""

    status_code = 503
    code = "PAYPAL_AUTHENTICATION_FAILED"
    message = "The server could not authenticate with PayPal. Check the PayPal Sandbox credentials."


class PayPalRateLimitedError(PayPalError):
    status_code = 429
    code = "PAYPAL_RATE_LIMITED"
    message = "PayPal is rate limiting requests. Please wait and try again."


class PayPalTimeoutError(PayPalError):
    status_code = 504
    code = "PAYPAL_TIMEOUT"
    message = "PayPal did not respond in time. Please try again."


class PayPalUnavailableError(PayPalError):
    """Connection failure or a PayPal server error (5xx)."""

    status_code = 503
    code = "PAYPAL_UNAVAILABLE"
    message = "PayPal is not reachable right now. Please try again later."


class PayPalResponseError(PayPalError):
    """PayPal answered with an unexpected status or a malformed body."""

    status_code = 502
    code = "PAYPAL_BAD_RESPONSE"
    message = "PayPal returned an unexpected response."


# ----------------------------------------------------------------- Orders (Step 3)


class PayPalInvalidRequestError(PayPalError):
    """Rejected locally before any HTTP request (e.g. malformed ID or request ID)."""

    status_code = 422
    code = "PAYPAL_INVALID_REQUEST"
    message = "The payment request is not valid for PayPal."


class PayPalCurrencyNotSupportedError(PayPalError):
    """Currency not allowed at the PayPal step. Raised before any HTTP request."""

    status_code = 422
    code = "PAYPAL_CURRENCY_NOT_SUPPORTED"
    message = "This currency cannot be sent to PayPal. Amounts are never converted."

    def __init__(self, currency: str) -> None:
        super().__init__()
        self.currency = currency
        # currency is a validated three-letter code, so it is safe to include.
        self.message = (
            f"{currency} payments cannot be sent to PayPal yet. "
            "Amounts are never converted to another currency."
        )
        self.args = (self.message,)


class PayPalPaymentDeclinedError(PayPalError):
    """PayPal answered, but the authorization or capture was declined/denied."""

    status_code = 422
    code = "PAYPAL_PAYMENT_DECLINED"
    message = "PayPal declined the payment."


# PayPal ``issue`` codes we recognise, with our own fixed messages. Only these
# codes are ever surfaced; any other PayPal error detail is discarded.
PAYPAL_ISSUE_MESSAGES: dict[str, str] = {
    "ORDER_NOT_APPROVED": "The buyer has not approved this order on PayPal yet.",
    "ORDER_ALREADY_AUTHORIZED": "This PayPal order has already been authorized.",
    "ORDER_ALREADY_CAPTURED": "This PayPal order has already been captured.",
    "ORDER_EXPIRED": "This PayPal order has expired.",
    "AUTHORIZATION_ALREADY_CAPTURED": "This authorization has already been captured.",
    "AUTHORIZATION_VOIDED": "This authorization has been voided.",
    "PREVIOUSLY_VOIDED": "This authorization has already been voided.",
    "AUTHORIZATION_EXPIRED": "This authorization has expired.",
    "CANNOT_BE_VOIDED": "This authorization can no longer be voided.",
    "CURRENCY_NOT_SUPPORTED": "PayPal does not support this currency for this account.",
    "INSTRUMENT_DECLINED": "The buyer's payment method was declined.",
    "PAYER_ACTION_REQUIRED": "The buyer must complete an action on PayPal first.",
    "DUPLICATE_INVOICE_ID": "A PayPal order with this reference already exists.",
    "RESOURCE_NOT_FOUND": "The PayPal order or authorization was not found.",
    "INVALID_RESOURCE_ID": "The PayPal order or authorization was not found.",
}


class PayPalRequestRejectedError(PayPalResponseError):
    """PayPal rejected a request (4xx) with a recognised issue code."""

    status_code = 422
    code = "PAYPAL_REQUEST_REJECTED"

    def __init__(self, issue: str, *, upstream_status: int | None = None) -> None:
        super().__init__(upstream_status=upstream_status)
        self.issue = issue
        self.message = PAYPAL_ISSUE_MESSAGES[issue]
        self.args = (self.message,)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, issue={self.issue!r}, "
            f"upstream_status={self.upstream_status!r})"
        )
