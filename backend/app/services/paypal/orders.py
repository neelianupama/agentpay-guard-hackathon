"""PayPal Orders v2 operations (Sandbox) built on :class:`PayPalClient`.

Flow supported: create order (intent AUTHORIZE) -> buyer approves on PayPal ->
authorize order -> capture *the authorization* (or void it).

Guarantees:

- Orders are always created with ``intent=AUTHORIZE``; this module has no way
  to create a CAPTURE-intent order or to capture an order directly.
- Amount and currency come only from validated backend ``TransactionDetails``.
  Only PayPal-allowed currencies are accepted; INR (and any other currency)
  is rejected *before* any HTTP request, and nothing is converted.
- Order, authorization and request IDs are validated before they are used in
  a URL path or header. Unsafe IDs are rejected before any HTTP request.
- Every PayPal response is validated (IDs, statuses, intent, amount and
  currency, approval URL host) before it is returned. Anything unexpected
  raises ``PayPalResponseError``; nothing is passed through unchecked.
- Every state-changing call sends a ``PayPal-Request-Id``. A fresh one is
  generated unless the caller supplies a stable one, which makes retries
  idempotent on PayPal's side (the same ID never creates a second order,
  authorization, capture or void).
- No risk decision, approval status or reviewer input is accepted here.
  Deciding *whether* a call is allowed belongs to the payment workflow
  (a later step).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from app.schemas.payment import TransactionDetails
from app.services.paypal.client import PayPalClient
from app.services.paypal.errors import (
    PayPalCurrencyNotSupportedError,
    PayPalInvalidRequestError,
    PayPalPaymentDeclinedError,
    PayPalResponseError,
)

logger = logging.getLogger(__name__)

ORDERS_PATH = "/v2/checkout/orders"
AUTHORIZATIONS_PATH = "/v2/payments/authorizations"
PREFER_REPRESENTATION = "return=representation"

# Currencies that may be sent to PayPal at this step. INR is deliberately
# excluded (architecture decision): it is rejected, never converted.
PAYPAL_ALLOWED_CURRENCIES = frozenset({"USD", "EUR", "GBP", "CAD", "AUD"})

# PayPal order/authorization/capture IDs are uppercase alphanumerics
# (17 characters today). Allow a little headroom, nothing else.
_PAYPAL_ID_RE = re.compile(r"^[A-Z0-9]{10,36}$")
# PayPal-Request-Id: max 108 characters; we allow a safe charset only.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,108}$")
_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_AMOUNT_RE = re.compile(r"^\d{1,13}(\.\d{1,2})?$")

APPROVAL_HOST = "www.sandbox.paypal.com"
APPROVAL_LINK_RELS = ("approve", "payer-action")

DESCRIPTION_MAX_LENGTH = 127  # PayPal purchase_unit.description limit

CREATED_ORDER_STATUSES = frozenset({"CREATED", "PAYER_ACTION_REQUIRED"})
ORDER_STATUSES = frozenset(
    {"CREATED", "SAVED", "APPROVED", "VOIDED", "COMPLETED", "PAYER_ACTION_REQUIRED"}
)
AUTHORIZATION_STATUSES = frozenset(
    {"CREATED", "CAPTURED", "DENIED", "PARTIALLY_CAPTURED", "VOIDED", "PENDING", "EXPIRED"}
)
AUTHORIZATION_OK_STATUSES = frozenset({"CREATED", "PENDING"})
CAPTURE_STATUSES = frozenset(
    {"COMPLETED", "DECLINED", "PARTIALLY_REFUNDED", "PENDING", "REFUNDED", "FAILED"}
)
CAPTURE_OK_STATUSES = frozenset({"COMPLETED", "PENDING"})


# --------------------------------------------------------------------- results


@dataclass(frozen=True)
class Money:
    currency: str
    value: Decimal

    def to_paypal(self) -> dict[str, str]:
        return {"currency_code": self.currency, "value": f"{self.value:.2f}"}


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    status: str
    approval_url: str
    amount: Money
    request_id: str


@dataclass(frozen=True)
class Authorization:
    authorization_id: str
    status: str
    amount: Money
    expiration_time: str | None


@dataclass(frozen=True)
class OrderDetails:
    order_id: str
    status: str
    intent: str
    amount: Money
    approval_url: str | None
    authorizations: tuple[Authorization, ...]


@dataclass(frozen=True)
class AuthorizedOrder:
    order_id: str
    order_status: str
    authorization: Authorization
    request_id: str


@dataclass(frozen=True)
class CaptureResult:
    capture_id: str
    authorization_id: str
    status: str
    amount: Money
    final_capture: bool
    request_id: str


@dataclass(frozen=True)
class VoidResult:
    authorization_id: str
    status: str
    request_id: str


# ------------------------------------------------------------------- validation


def validate_paypal_id(value: object) -> str:
    """Return ``value`` if it is a safe PayPal ID, else raise before any request."""
    if not isinstance(value, str) or not _PAYPAL_ID_RE.fullmatch(value):
        raise PayPalInvalidRequestError()
    return value


def new_request_id(operation: str) -> str:
    return f"agentpay-{operation}-{uuid.uuid4().hex}"


def resolve_request_id(request_id: str | None, operation: str) -> str:
    """Use a caller-supplied stable request ID (validated) or generate a fresh one."""
    if request_id is None:
        return new_request_id(operation)
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
        raise PayPalInvalidRequestError()
    return request_id


def order_money(details: TransactionDetails) -> Money:
    """Amount/currency for PayPal from validated backend data. INR is rejected here."""
    if details.currency not in PAYPAL_ALLOWED_CURRENCIES:
        raise PayPalCurrencyNotSupportedError(details.currency)
    amount = details.amount
    if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise PayPalInvalidRequestError()
    return Money(details.currency, amount.quantize(Decimal("0.01")))


def purchase_description(details: TransactionDetails) -> str:
    text = f"AgentPay Guard payment to {details.merchant_name}: {details.description}"
    text = " ".join(text.split())  # collapse whitespace/newlines
    if len(text) > DESCRIPTION_MAX_LENGTH:
        text = text[: DESCRIPTION_MAX_LENGTH - 1].rstrip() + "…"
    return text


def _bad(what: str) -> PayPalResponseError:
    logger.warning("PayPal: unexpected Orders response (%s)", what)
    return PayPalResponseError()


def _str(data: dict[str, Any], key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise _bad(what)
    return value


def _id(data: dict[str, Any], key: str, what: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not _PAYPAL_ID_RE.fullmatch(value):
        raise _bad(what)
    return value


def _money(data: object, what: str) -> Money:
    if not isinstance(data, dict):
        raise _bad(what)
    currency, value = data.get("currency_code"), data.get("value")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise _bad(what)
    if not isinstance(value, str) or not _AMOUNT_RE.fullmatch(value):
        raise _bad(what)
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise _bad(what) from None
    if amount <= 0:
        raise _bad(what)
    return Money(currency, amount)


def _expect_money(actual: Money, expected: Money, what: str) -> None:
    if actual.currency != expected.currency or actual.value != expected.value:
        raise _bad(f"{what} amount mismatch")


def _purchase_unit(body: dict[str, Any], what: str) -> dict[str, Any]:
    units = body.get("purchase_units")
    if not isinstance(units, list) or len(units) != 1 or not isinstance(units[0], dict):
        raise _bad(f"{what} purchase_units")
    return units[0]


def _approval_url(body: dict[str, Any], *, required: bool) -> str | None:
    links = body.get("links")
    if not isinstance(links, list):
        if required:
            raise _bad("links")
        return None
    for link in links:
        if not isinstance(link, dict) or link.get("rel") not in APPROVAL_LINK_RELS:
            continue
        href = link.get("href")
        if not isinstance(href, str):
            raise _bad("approval link")
        parts = urlsplit(href)
        if parts.scheme != "https" or parts.hostname != APPROVAL_HOST or parts.username or parts.port:
            raise _bad("approval link host")
        return href
    if required:
        raise _bad("approval link missing")
    return None


def _authorization(data: object, what: str) -> Authorization:
    if not isinstance(data, dict):
        raise _bad(what)
    status = _str(data, "status", what)
    if status not in AUTHORIZATION_STATUSES:
        raise _bad(f"{what} status")
    expiration = data.get("expiration_time")
    if expiration is not None and not isinstance(expiration, str):
        raise _bad(f"{what} expiration_time")
    return Authorization(
        authorization_id=_id(data, "id", what),
        status=status,
        amount=_money(data.get("amount"), what),
        expiration_time=expiration,
    )


def _authorizations(unit: dict[str, Any], what: str) -> tuple[Authorization, ...]:
    payments = unit.get("payments")
    if payments is None:
        return ()
    if not isinstance(payments, dict):
        raise _bad(what)
    items = payments.get("authorizations", [])
    if not isinstance(items, list):
        raise _bad(what)
    return tuple(_authorization(item, what) for item in items)


# ---------------------------------------------------------------------- service


class PayPalOrdersService:
    """Orders v2 operations. Holds no state besides the shared ``PayPalClient``."""

    def __init__(self, client: PayPalClient) -> None:
        self._client = client

    async def create_order(
        self,
        details: TransactionDetails,
        *,
        reference_id: str | None = None,
        request_id: str | None = None,
    ) -> CreatedOrder:
        """Create an AUTHORIZE-intent order for the buyer to approve on PayPal."""
        money = order_money(details)  # rejects INR before any HTTP request
        if reference_id is not None and not _REFERENCE_ID_RE.fullmatch(reference_id):
            raise PayPalInvalidRequestError()
        rid = resolve_request_id(request_id, "create-order")

        unit: dict[str, Any] = {
            "description": purchase_description(details),
            "amount": money.to_paypal(),
        }
        if reference_id is not None:
            unit["reference_id"] = reference_id
            unit["custom_id"] = reference_id
        payload = {"intent": "AUTHORIZE", "purchase_units": [unit]}

        body = await self._client.request(
            "POST", ORDERS_PATH, json=payload, request_id=rid, prefer=PREFER_REPRESENTATION
        )
        order_id = _id(body, "id", "order id")
        status = _str(body, "status", "order status")
        if status not in CREATED_ORDER_STATUSES:
            raise _bad("order status")
        if body.get("intent") != "AUTHORIZE":
            raise _bad("order intent")
        _expect_money(_money(_purchase_unit(body, "order").get("amount"), "order"), money, "order")
        approval_url = _approval_url(body, required=True)
        assert approval_url is not None
        logger.info("PayPal: order created (status=%s)", status)
        return CreatedOrder(order_id, status, approval_url, money, rid)

    async def get_order(self, order_id: str) -> OrderDetails:
        oid = validate_paypal_id(order_id)
        body = await self._client.request("GET", f"{ORDERS_PATH}/{oid}")
        if _id(body, "id", "order id") != oid:
            raise _bad("order id mismatch")
        status = _str(body, "status", "order status")
        if status not in ORDER_STATUSES:
            raise _bad("order status")
        if body.get("intent") != "AUTHORIZE":
            raise _bad("order intent")
        unit = _purchase_unit(body, "order")
        return OrderDetails(
            order_id=oid,
            status=status,
            intent="AUTHORIZE",
            amount=_money(unit.get("amount"), "order"),
            approval_url=_approval_url(body, required=False),
            authorizations=_authorizations(unit, "order authorization"),
        )

    async def authorize_order(
        self,
        order_id: str,
        *,
        expected_amount: Money,
        request_id: str | None = None,
    ) -> AuthorizedOrder:
        """Authorize a buyer-approved order; validates status and the exact amount."""
        oid = validate_paypal_id(order_id)
        rid = resolve_request_id(request_id, "authorize")
        body = await self._client.request(
            "POST",
            f"{ORDERS_PATH}/{oid}/authorize",
            json={},
            request_id=rid,
            prefer=PREFER_REPRESENTATION,
        )
        if _id(body, "id", "order id") != oid:
            raise _bad("order id mismatch")
        order_status = _str(body, "status", "order status")
        if order_status != "COMPLETED":
            raise _bad("authorize order status")
        authorizations = _authorizations(_purchase_unit(body, "authorize"), "authorization")
        if len(authorizations) != 1:
            raise _bad("authorization count")
        authorization = authorizations[0]
        _expect_money(authorization.amount, expected_amount, "authorization")
        if authorization.status not in AUTHORIZATION_OK_STATUSES:
            logger.warning("PayPal: authorization not usable (status=%s)", authorization.status)
            raise PayPalPaymentDeclinedError()
        logger.info("PayPal: order authorized (authorization_status=%s)", authorization.status)
        return AuthorizedOrder(oid, order_status, authorization, rid)

    async def capture_authorization(
        self,
        authorization_id: str,
        *,
        expected_amount: Money,
        request_id: str | None = None,
    ) -> CaptureResult:
        """Capture the full authorized amount. Orders are never captured directly."""
        aid = validate_paypal_id(authorization_id)
        rid = resolve_request_id(request_id, "capture")
        body = await self._client.request(
            "POST",
            f"{AUTHORIZATIONS_PATH}/{aid}/capture",
            json={"amount": expected_amount.to_paypal(), "final_capture": True},
            request_id=rid,
            prefer=PREFER_REPRESENTATION,
        )
        capture_id = _id(body, "id", "capture id")
        status = _str(body, "status", "capture status")
        if status not in CAPTURE_STATUSES:
            raise _bad("capture status")
        _expect_money(_money(body.get("amount"), "capture"), expected_amount, "capture")
        final_capture = body.get("final_capture", True)
        if final_capture is not True:
            raise _bad("final_capture")
        if status not in CAPTURE_OK_STATUSES:
            logger.warning("PayPal: capture not successful (status=%s)", status)
            raise PayPalPaymentDeclinedError()
        logger.info("PayPal: authorization captured (status=%s)", status)
        return CaptureResult(capture_id, aid, status, expected_amount, True, rid)

    async def void_authorization(
        self, authorization_id: str, *, request_id: str | None = None
    ) -> VoidResult:
        aid = validate_paypal_id(authorization_id)
        rid = resolve_request_id(request_id, "void")
        body = await self._client.request(
            "POST",
            f"{AUTHORIZATIONS_PATH}/{aid}/void",
            request_id=rid,
            prefer=PREFER_REPRESENTATION,
        )
        if body:  # 200 with representation; 204 (empty) also means voided
            if _id(body, "id", "void authorization id") != aid:
                raise _bad("void authorization id mismatch")
            if _str(body, "status", "void status") != "VOIDED":
                raise _bad("void status")
        logger.info("PayPal: authorization voided")
        return VoidResult(aid, "VOIDED", rid)
