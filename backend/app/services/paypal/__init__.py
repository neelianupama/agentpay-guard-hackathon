"""PayPal Sandbox integration (server-side only).

Credentials and access tokens live only in backend memory; they are never
sent to, or readable by, the frontend.
"""

from app.services.paypal.client import TOKEN_PATH, AccessToken, PayPalClient
from app.services.paypal.errors import (
    PayPalAuthenticationError,
    PayPalCurrencyNotSupportedError,
    PayPalError,
    PayPalInvalidRequestError,
    PayPalNotConfiguredError,
    PayPalPaymentDeclinedError,
    PayPalRateLimitedError,
    PayPalRequestRejectedError,
    PayPalResponseError,
    PayPalTimeoutError,
    PayPalUnavailableError,
)
from app.services.paypal.orders import (
    PAYPAL_ALLOWED_CURRENCIES,
    Authorization,
    AuthorizedOrder,
    CaptureResult,
    CreatedOrder,
    Money,
    OrderDetails,
    PayPalOrdersService,
    VoidResult,
)

__all__ = [
    "PAYPAL_ALLOWED_CURRENCIES",
    "TOKEN_PATH",
    "AccessToken",
    "Authorization",
    "AuthorizedOrder",
    "CaptureResult",
    "CreatedOrder",
    "Money",
    "OrderDetails",
    "PayPalAuthenticationError",
    "PayPalClient",
    "PayPalCurrencyNotSupportedError",
    "PayPalError",
    "PayPalInvalidRequestError",
    "PayPalNotConfiguredError",
    "PayPalOrdersService",
    "PayPalPaymentDeclinedError",
    "PayPalRateLimitedError",
    "PayPalRequestRejectedError",
    "PayPalResponseError",
    "PayPalTimeoutError",
    "PayPalUnavailableError",
    "VoidResult",
]
