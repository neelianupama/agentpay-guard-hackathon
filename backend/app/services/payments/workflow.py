"""Payment workflow: deterministic risk decision -> PayPal Orders v2 (Sandbox).

Rules enforced here (independently of the API layer):

- The backend risk engine decides. BLOCK is stored as BLOCKED and never sent to
  PayPal. REVIEW is stored as PENDING_REVIEW and sent to PayPal only after a
  reviewer approves. ALLOW creates an AUTHORIZE-intent order immediately.
- Currencies PayPal may not receive (INR) are rejected before any PayPal call.
- Buyer approval is read from PayPal (``get_order``), never from the client.
- Funds are captured only by an explicit reviewer capture action; never
  automatically. PENDING results stay pending.
- Each PayPal operation's ``PayPal-Request-Id`` is generated once, saved
  *before* the call, and reused for every retry, so an ambiguous failure
  (timeout, 5xx, unreadable response) can be retried without duplicating an
  order, authorization, capture or void.
- One action per record at a time (``repository.locked``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import NoReturn

from app.schemas.common import utc_now
from app.schemas.enums import ApprovalDecision, PaymentStatus, PolicyDecision
from app.schemas.payment import TransactionDetails
from app.services.audit import AuditEventType as E
from app.services.audit import AuditOperation as Op
from app.services.audit import AuditRecorder, payment_event
from app.services.payments.errors import (
    BuyerApprovalRequiredError,
    InvalidPaymentTransitionError,
    PaymentProviderError,
    PaymentWorkflowError,
)
from app.services.payments.records import (
    LastError,
    PaymentRecord,
    ReviewDecision,
    ReviewerAction,
    require_status,
    transition,
)
from app.services.payments.repository import InMemoryPaymentRepository, new_transaction_id
from app.services.paypal import (
    PAYPAL_ALLOWED_CURRENCIES,
    Money,
    PayPalCurrencyNotSupportedError,
    PayPalError,
    PayPalInvalidRequestError,
    PayPalOrdersService,
    PayPalPaymentDeclinedError,
    PayPalRequestRejectedError,
    PayPalResponseError,
)
from app.services.paypal.orders import new_request_id
from app.services.risk import RiskEngine

logger = logging.getLogger(__name__)

S = PaymentStatus

# PayPal answered and definitively refused: retrying the same request cannot succeed.
DEFINITIVE_ERRORS: tuple[type[PayPalError], ...] = (
    PayPalRequestRejectedError,
    PayPalPaymentDeclinedError,
    PayPalCurrencyNotSupportedError,
    PayPalInvalidRequestError,
)

OrdersFactory = Callable[[], PayPalOrdersService]


class PaymentWorkflow:
    def __init__(
        self,
        repository: InMemoryPaymentRepository,
        risk_engine: RiskEngine,
        orders_factory: OrdersFactory,
        clock: Callable[[], datetime] = utc_now,
        audit: AuditRecorder | None = None,
    ) -> None:
        # Audit events are emitted without waiting; Elastic problems never affect payments.
        self._audit = audit or AuditRecorder(None)
        self._repo = repository
        self._risk = risk_engine
        self._orders_factory = orders_factory
        self._clock = clock

    # ------------------------------------------------------------------- reads

    def get(self, transaction_id: str) -> PaymentRecord:
        return self._repo.get(transaction_id)

    def list(self, *, limit: int, offset: int) -> tuple[list[PaymentRecord], int]:
        return self._repo.list(limit=limit, offset=offset)

    # ------------------------------------------------------------------ create

    async def create_payment(self, details: TransactionDetails) -> PaymentRecord:
        assessment = self._risk.assess(details)  # authoritative; nothing client-supplied
        if assessment.decision is PolicyDecision.BLOCK:
            status = S.BLOCKED
        else:
            if details.currency not in PAYPAL_ALLOWED_CURRENCIES:
                # Before any record or PayPal call: never converted, never sent.
                raise PaymentProviderError(
                    PayPalCurrencyNotSupportedError(details.currency), retry_safe=False
                )
            status = (
                S.PENDING_REVIEW
                if assessment.decision is PolicyDecision.REVIEW
                else S.APPROVED_FOR_PAYMENT
            )

        now = self._clock()
        record = PaymentRecord(
            transaction_id=new_transaction_id(),
            merchant_name=details.merchant_name,
            description=details.description,
            purpose=details.purpose,
            amount=details.amount,
            currency=details.currency,
            risk_score=assessment.risk_score,
            risk_level=assessment.risk_level,
            decision=assessment.decision,
            reasons=tuple(assessment.reasons),
            assessed_at=assessment.assessed_at,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self._repo.add(record)
        self._emit(record, E.PAYMENT_CREATED, reasons=list(record.reasons))
        logger.info("Payment %s created (decision=%s, status=%s)",
                    record.transaction_id, record.decision, record.status)
        if status is S.APPROVED_FOR_PAYMENT:
            return await self.create_order(record.transaction_id)
        return record

    async def create_order(self, transaction_id: str) -> PaymentRecord:
        """Create (or safely retry creating) the AUTHORIZE-intent PayPal order."""
        async with self._repo.locked(transaction_id) as record:
            require_status(record, S.APPROVED_FOR_PAYMENT, action="create a PayPal order for")
            self._assert_payable(record)
            if record.create_request_id is None:
                record.create_request_id = new_request_id("create-order")
                self._repo.save(record)  # persist before calling PayPal
            try:
                created = await self._orders().create_order(
                    _details(record),
                    reference_id=record.transaction_id,
                    request_id=record.create_request_id,
                )
            except PayPalError as exc:
                self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.CREATE_ORDER)
            record.paypal_order_id = created.order_id
            record.paypal_approval_url = created.approval_url
            record.paypal_order_status = created.status
            record.last_error = None
            self._move(record, S.AWAITING_BUYER_APPROVAL)
            self._emit(record, E.PAYPAL_ORDER_CREATED, operation=Op.CREATE_ORDER, outcome="CREATED",
                       paypal_order_id=created.order_id, paypal_status=created.status)
            return record

    # ---------------------------------------------------------------- reviewer

    async def approve(
        self, transaction_id: str, *, reviewer_name: str | None, comment: str | None
    ) -> PaymentRecord:
        async with self._repo.locked(transaction_id) as record:
            require_status(record, S.PENDING_REVIEW, action="approve")
            self._assert_payable(record)
            now = self._clock()
            record.review = ReviewDecision(ApprovalDecision.APPROVE, reviewer_name, comment, now)
            record.reviewer_actions.append(ReviewerAction("approve", reviewer_name, comment, now))
            self._move(record, S.APPROVED_FOR_PAYMENT)
            self._emit(record, E.REVIEW_APPROVED, reviewer_name=reviewer_name)
        return await self.create_order(transaction_id)

    async def reject(
        self, transaction_id: str, *, reviewer_name: str | None, comment: str | None
    ) -> PaymentRecord:
        async with self._repo.locked(transaction_id) as record:
            require_status(record, S.PENDING_REVIEW, action="reject")
            now = self._clock()
            record.review = ReviewDecision(ApprovalDecision.REJECT, reviewer_name, comment, now)
            record.reviewer_actions.append(ReviewerAction("reject", reviewer_name, comment, now))
            self._move(record, S.REJECTED)
            self._emit(record, E.REVIEW_REJECTED, reviewer_name=reviewer_name)
            return record

    # ------------------------------------------------------------- authorization

    async def authorize(self, transaction_id: str) -> PaymentRecord:
        """Verify buyer approval with PayPal, then authorize (never capture)."""
        async with self._repo.locked(transaction_id) as record:
            require_status(
                record,
                S.AWAITING_BUYER_APPROVAL,
                S.BUYER_APPROVED,
                S.AUTHORIZATION_PENDING,
                action="authorize",
            )
            assert record.paypal_order_id is not None
            if record.status is S.AWAITING_BUYER_APPROVAL:
                await self._confirm_buyer_approval(record)
            if record.status is S.BUYER_APPROVED:
                await self._authorize_order(record)
            elif record.status is S.AUTHORIZATION_PENDING:
                await self._refresh_pending_authorization(record)
            return record

    async def _confirm_buyer_approval(self, record: PaymentRecord) -> None:
        try:
            order = await self._orders().get_order(record.paypal_order_id or "")
        except PayPalError as exc:
            self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.CHECK_BUYER_APPROVAL)
        if order.order_id != record.paypal_order_id or order.amount != _money(record):
            self._handle_paypal_error(record, _mismatch(), on_definitive=None, operation=Op.CHECK_BUYER_APPROVAL)
        record.paypal_order_status = order.status
        if order.status == "VOIDED":
            self._record_error(record, "PAYPAL_ORDER_VOIDED",
                               "The PayPal order was voided or expired.", retry_safe=False)
            self._move(record, S.FAILED)
            self._emit(record, E.PAYPAL_AUTHORIZATION, operation=Op.CHECK_BUYER_APPROVAL,
                       outcome="ORDER_VOIDED", paypal_order_id=record.paypal_order_id,
                       paypal_status=order.status, error_code="PAYPAL_ORDER_VOIDED", retry_safe=False)
            raise InvalidPaymentTransitionError(
                "The PayPal order was voided or expired.",
                transaction_id=record.transaction_id, status=str(record.status),
            )
        if order.status != "APPROVED":
            record.updated_at = self._clock()
            self._repo.save(record)
            self._emit(record, E.PAYPAL_AUTHORIZATION, operation=Op.CHECK_BUYER_APPROVAL,
                       outcome="BUYER_APPROVAL_REQUIRED", paypal_order_id=record.paypal_order_id,
                       paypal_status=order.status)
            raise BuyerApprovalRequiredError(
                transaction_id=record.transaction_id, paypal_order_status=order.status
            )
        if record.authorize_request_id is None:
            record.authorize_request_id = new_request_id("authorize")
        self._move(record, S.BUYER_APPROVED)  # persisted before authorizing
        self._emit(record, E.PAYPAL_AUTHORIZATION, operation=Op.CHECK_BUYER_APPROVAL,
                   outcome="BUYER_APPROVED", paypal_order_id=record.paypal_order_id,
                   paypal_status=order.status)

    async def _authorize_order(self, record: PaymentRecord) -> None:
        try:
            result = await self._orders().authorize_order(
                record.paypal_order_id or "",
                expected_amount=_money(record),
                request_id=record.authorize_request_id,
            )
        except PayPalError as exc:
            self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.AUTHORIZE)
        auth = result.authorization
        record.paypal_order_status = result.order_status
        record.paypal_authorization_id = auth.authorization_id
        record.paypal_authorization_status = auth.status
        record.last_error = None
        self._move(record, S.AUTHORIZED if auth.status == "CREATED" else S.AUTHORIZATION_PENDING)
        self._emit(record, E.PAYPAL_AUTHORIZATION, operation=Op.AUTHORIZE,
                   outcome="AUTHORIZED" if auth.status == "CREATED" else "AUTHORIZATION_PENDING",
                   paypal_order_id=record.paypal_order_id,
                   paypal_authorization_id=auth.authorization_id, paypal_status=auth.status)

    async def _refresh_pending_authorization(self, record: PaymentRecord) -> None:
        try:
            order = await self._orders().get_order(record.paypal_order_id or "")
        except PayPalError as exc:
            self._handle_paypal_error(record, exc, on_definitive=None, operation=Op.REFRESH_AUTHORIZATION)
        matches = [a for a in order.authorizations
                   if a.authorization_id == record.paypal_authorization_id]
        if len(matches) != 1 or matches[0].amount != _money(record):
            self._handle_paypal_error(record, _mismatch(), on_definitive=None, operation=Op.REFRESH_AUTHORIZATION)
        status = matches[0].status
        record.paypal_order_status = order.status
        record.paypal_authorization_status = status
        self._emit(record, E.PAYPAL_AUTHORIZATION, operation=Op.REFRESH_AUTHORIZATION,
                   outcome={"CREATED": "AUTHORIZED", "PENDING": "AUTHORIZATION_PENDING"}.get(status, "NOT_USABLE"),
                   paypal_order_id=record.paypal_order_id,
                   paypal_authorization_id=record.paypal_authorization_id, paypal_status=status)
        if status == "CREATED":
            record.last_error = None
            self._move(record, S.AUTHORIZED)
        elif status == "PENDING":
            record.updated_at = self._clock()
            self._repo.save(record)
        else:  # DENIED, VOIDED, EXPIRED, CAPTURED...: not usable by this workflow
            self._record_error(record, "PAYPAL_AUTHORIZATION_NOT_USABLE",
                               "PayPal reports the authorization can no longer be used.",
                               retry_safe=False)
            self._move(record, S.FAILED)

    # -------------------------------------------------------- capture and void

    async def capture(
        self, transaction_id: str, *, reviewer_name: str | None, comment: str | None
    ) -> PaymentRecord:
        """Explicit reviewer capture of the stored authorization. Never automatic."""
        async with self._repo.locked(transaction_id) as record:
            require_status(record, S.AUTHORIZED, S.PENDING_CAPTURE, action="capture")
            if record.paypal_authorization_id is None:
                raise InvalidPaymentTransitionError(transaction_id=transaction_id)
            record.reviewer_actions.append(
                ReviewerAction("capture", reviewer_name, comment, self._clock())
            )
            if record.status is S.AUTHORIZED:
                if record.capture_request_id is None:
                    record.capture_request_id = new_request_id("capture")
                self._move(record, S.PENDING_CAPTURE)  # persisted before capturing
            else:
                self._repo.save(record)  # retry: same stored request ID
            try:
                result = await self._orders().capture_authorization(
                    record.paypal_authorization_id,
                    expected_amount=_money(record),
                    request_id=record.capture_request_id,
                )
            except PayPalError as exc:
                self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.CAPTURE,
                                          reviewer_name=reviewer_name)
            record.paypal_capture_id = result.capture_id
            record.paypal_capture_status = result.status
            record.last_error = None
            self._move(record, S.CAPTURED if result.status == "COMPLETED" else S.CAPTURE_PENDING)
            self._emit(record, E.PAYPAL_CAPTURE, operation=Op.CAPTURE,
                       outcome="CAPTURED" if result.status == "COMPLETED" else "CAPTURE_PENDING",
                       paypal_authorization_id=record.paypal_authorization_id,
                       paypal_capture_id=result.capture_id, paypal_status=result.status,
                       reviewer_name=reviewer_name)
            return record

    async def void(
        self, transaction_id: str, *, reviewer_name: str | None, comment: str | None
    ) -> PaymentRecord:
        async with self._repo.locked(transaction_id) as record:
            require_status(record, S.AUTHORIZED, S.PENDING_VOID, action="void")
            if record.paypal_authorization_id is None:
                raise InvalidPaymentTransitionError(transaction_id=transaction_id)
            record.reviewer_actions.append(
                ReviewerAction("void", reviewer_name, comment, self._clock())
            )
            if record.status is S.AUTHORIZED:
                if record.void_request_id is None:
                    record.void_request_id = new_request_id("void")
                self._move(record, S.PENDING_VOID)
            else:
                self._repo.save(record)
            try:
                result = await self._orders().void_authorization(
                    record.paypal_authorization_id, request_id=record.void_request_id
                )
            except PayPalRequestRejectedError as exc:
                if exc.issue == "PREVIOUSLY_VOIDED":
                    record.paypal_authorization_status = "VOIDED"
                    record.last_error = None
                    self._move(record, S.VOIDED)
                    self._emit_void(record, "PREVIOUSLY_VOIDED", reviewer_name)
                    return record
                self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.VOID,
                                          reviewer_name=reviewer_name)
            except PayPalError as exc:
                self._handle_paypal_error(record, exc, on_definitive=S.FAILED, operation=Op.VOID,
                                          reviewer_name=reviewer_name)
            record.paypal_authorization_status = result.status
            record.last_error = None
            self._move(record, S.VOIDED)
            self._emit_void(record, "VOIDED", reviewer_name)
            return record

    # ----------------------------------------------------------------- helpers

    def _orders(self) -> PayPalOrdersService:
        return self._orders_factory()  # may raise PayPalNotConfiguredError (a PayPalError)

    def _assert_payable(self, record: PaymentRecord) -> None:
        if record.decision is PolicyDecision.BLOCK:  # defence in depth
            raise InvalidPaymentTransitionError(transaction_id=record.transaction_id)
        if record.currency not in PAYPAL_ALLOWED_CURRENCIES:
            raise PaymentProviderError(
                PayPalCurrencyNotSupportedError(record.currency), retry_safe=False,
                transaction_id=record.transaction_id,
            )

    def _move(self, record: PaymentRecord, new_status: PaymentStatus) -> None:
        old = record.status
        transition(record, new_status, at=self._clock())
        self._repo.save(record)
        logger.info("Payment %s: %s -> %s", record.transaction_id, old, new_status)
        self._emit(record, E.PAYMENT_STATUS_CHANGED, previous_status=old)

    def _emit(self, record: PaymentRecord, event_type: E, **fields: object) -> None:
        """Queue an allowlisted audit event. Never raises: auditing must not break payments."""
        try:
            self._audit.emit(payment_event(record, event_type, **fields))
        except Exception:  # noqa: BLE001
            logger.warning("Payment %s: could not build audit event %s", record.transaction_id, event_type)

    def _emit_void(self, record: PaymentRecord, outcome: str, reviewer_name: str | None) -> None:
        self._emit(record, E.PAYPAL_VOID, operation=Op.VOID, outcome=outcome,
                   paypal_authorization_id=record.paypal_authorization_id, paypal_status="VOIDED",
                   reviewer_name=reviewer_name)

    def _record_error(self, record: PaymentRecord, code: str, message: str, *, retry_safe: bool) -> None:
        record.last_error = LastError(code, message, retry_safe, self._clock())

    def _handle_paypal_error(
        self,
        record: PaymentRecord,
        exc: PayPalError,
        *,
        on_definitive: PaymentStatus | None,
        operation: Op,
        reviewer_name: str | None = None,
    ) -> NoReturn:
        """Persist a safe outcome, then raise a client-safe error.

        Definitive refusals move the record to ``on_definitive`` (if given).
        Anything else is treated as ambiguous: the status and the stored
        request ID are kept so the same action can be retried safely.
        """
        definitive = isinstance(exc, DEFINITIVE_ERRORS)
        retry_safe = not definitive
        self._record_error(record, exc.code, exc.message, retry_safe=retry_safe)
        if definitive and on_definitive is not None:
            self._move(record, on_definitive)
        else:
            record.updated_at = self._clock()
            self._repo.save(record)
        logger.warning("Payment %s: PayPal %s (%s, retry_safe=%s)",
                       record.transaction_id, exc.code, record.status, retry_safe)
        self._emit(record, E.PAYPAL_OPERATION_FAILED, operation=operation, outcome="FAILED",
                   error_code=exc.code, retry_safe=retry_safe,
                   paypal_order_id=record.paypal_order_id,
                   paypal_authorization_id=record.paypal_authorization_id,
                   reviewer_name=reviewer_name)
        raise PaymentProviderError(
            exc, retry_safe=retry_safe,
            transaction_id=record.transaction_id, status=str(record.status),
        )


def _money(record: PaymentRecord) -> Money:
    return Money(record.currency, record.amount.quantize(Decimal("0.01")))


def _details(record: PaymentRecord) -> TransactionDetails:
    return TransactionDetails(
        merchant_name=record.merchant_name,
        amount=record.amount,
        currency=record.currency,
        description=record.description,
        purpose=record.purpose,
    )


def _mismatch() -> PayPalError:
    """PayPal's data does not match the stored record: treat as an unreliable response."""
    return PayPalResponseError()


__all__ = ["PaymentWorkflow", "PaymentWorkflowError"]
