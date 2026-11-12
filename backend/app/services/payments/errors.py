"""Payment workflow errors with fixed, client-safe messages."""

from __future__ import annotations

from typing import Any

from app.services.paypal.errors import PayPalError


class PaymentWorkflowError(Exception):
    status_code: int = 409
    code: str = "PAYMENT_ERROR"
    message: str = "The payment action could not be completed."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)
        self.details = details


class PaymentNotFoundError(PaymentWorkflowError):
    status_code = 404
    code = "PAYMENT_NOT_FOUND"
    message = "Payment not found."


class InvalidPaymentTransitionError(PaymentWorkflowError):
    status_code = 409
    code = "PAYMENT_INVALID_STATE"
    message = "This action is not allowed in the payment's current status."


class PaymentBusyError(PaymentWorkflowError):
    status_code = 409
    code = "PAYMENT_OPERATION_IN_PROGRESS"
    message = "Another action on this payment is in progress. Try again shortly."


class BuyerApprovalRequiredError(PaymentWorkflowError):
    status_code = 409
    code = "PAYMENT_BUYER_APPROVAL_REQUIRED"
    message = "PayPal does not report this order as approved by the buyer yet."


class PaymentProviderError(PaymentWorkflowError):
    """A PayPal error during a workflow step, after the record was updated safely."""

    def __init__(self, error: PayPalError, *, retry_safe: bool, **details: Any) -> None:
        self.status_code = error.status_code
        self.code = error.code
        super().__init__(error.message, retry_safe=retry_safe, **details)
        self.paypal_error = error
