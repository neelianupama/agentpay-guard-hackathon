"""PayPal Orders v2 service (Step 3). All HTTP is mocked with httpx.MockTransport.

``FakePayPal`` is a small in-memory stand-in for the Sandbox API. It honours
``PayPal-Request-Id`` like PayPal does (a repeated ID replays the original
response without creating anything new), so duplicate-retry behaviour can be
tested. No real or Sandbox network requests are made.
"""

import asyncio
import base64
import inspect
import json
import logging
import re
from decimal import Decimal

import httpx
import pytest

from app.schemas import TransactionDetails
from app.services.paypal import (
    PAYPAL_ALLOWED_CURRENCIES,
    TOKEN_PATH,
    Money,
    PayPalClient,
    PayPalCurrencyNotSupportedError,
    PayPalInvalidRequestError,
    PayPalOrdersService,
    PayPalPaymentDeclinedError,
    PayPalRequestRejectedError,
    PayPalResponseError,
    PayPalTimeoutError,
)
from app.services.paypal import orders as orders_module

SANDBOX = "https://api-m.sandbox.paypal.com"
CLIENT_ID = "FAKE-client-id-1234567890"
SECRET = "FAKE-client-secret-0987654321"
TOKEN = "A21AA-FAKE-ACCESS-TOKEN-abcdef"
BASIC = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
SENSITIVE = "SENSITIVE-RAW-PAYPAL-BODY"
FORBIDDEN = (CLIENT_ID, SECRET, TOKEN, BASIC, SENSITIVE)

ORDER_ID = "5O190127TN364715T"
AUTH_ID = "0VF52814937998046"
CAPTURE_ID = "3C679366HH908993F"
APPROVE_URL = f"https://www.sandbox.paypal.com/checkoutnow?token={ORDER_ID}"
USD_250 = Money("USD", Decimal("250.00"))
GENERATED_ID = re.compile(r"^agentpay-(create-order|authorize|capture|void)-[0-9a-f]{32}$")


def details(**overrides) -> TransactionDetails:
    base = {
        "merchant_name": "ABC Technologies",
        "amount": "250",
        "currency": "USD",
        "description": "Software services",
        "purpose": "Engineering tooling",
    }
    return TransactionDetails(**{**base, **overrides})


def run(coro):
    return asyncio.run(coro)


def assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in FORBIDDEN:
            assert secret not in text, f"leaked {secret!r}"


def amount_json(money: Money) -> dict:
    return {"currency_code": money.currency, "value": f"{money.value:.2f}"}


def order_body(*, order_id=ORDER_ID, status="PAYER_ACTION_REQUIRED", intent="AUTHORIZE",
               money=USD_250, links=None, payments=None) -> dict:
    unit: dict = {"reference_id": "default", "amount": amount_json(money)}
    if payments is not None:
        unit["payments"] = payments
    return {
        "id": order_id,
        "status": status,
        "intent": intent,
        "purchase_units": [unit],
        "links": links if links is not None else [
            {"href": f"{SANDBOX}/v2/checkout/orders/{order_id}", "rel": "self", "method": "GET"},
            {"href": APPROVE_URL, "rel": "payer-action", "method": "GET"},
        ],
    }


def auth_json(*, auth_id=AUTH_ID, status="CREATED", money=USD_250) -> dict:
    return {"id": auth_id, "status": status, "amount": amount_json(money),
            "expiration_time": "2026-12-11T10:00:00Z"}


def authorized_body(*, order_status="COMPLETED", auths=None) -> dict:
    return order_body(status=order_status, links=[],
                      payments={"authorizations": auths if auths is not None else [auth_json()]})


def capture_body(*, status="COMPLETED", money=USD_250, final_capture=True) -> dict:
    return {"id": CAPTURE_ID, "status": status, "amount": amount_json(money),
            "final_capture": final_capture}


def error_body(issue: str, status_name: str = "UNPROCESSABLE_ENTITY") -> dict:
    return {"name": status_name, "message": f"{SENSITIVE} message",
            "details": [{"issue": issue, "description": f"{SENSITIVE} detail"}],
            "debug_id": "FAKE-debug"}


class FakePayPal:
    """Routes token + Orders requests; honours PayPal-Request-Id idempotency."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[tuple[str, str], object] = {}
        self.idempotent: dict[str, httpx.Response] = {}
        self.created = {"orders": 0, "authorizations": 0, "captures": 0, "voids": 0}
        self.fail_after_processing: set[str] = set()  # request IDs that "time out" once

    def on(self, method: str, path: str, response) -> "FakePayPal":
        self.routes[(method, path)] = response
        return self

    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != TOKEN_PATH]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer",
                                             "expires_in": 32400})
        rid = request.headers.get("paypal-request-id")
        if rid and rid in self.idempotent:
            return self.idempotent[rid]  # PayPal replays the original result
        handler = self.routes.get((request.method, request.url.path))
        if handler is None:
            return httpx.Response(404, json=error_body("RESOURCE_NOT_FOUND", "RESOURCE_NOT_FOUND"))
        response = handler(request) if callable(handler) else handler
        if response.status_code < 300:
            kind = self._kind(request)
            if kind:
                self.created[kind] += 1
        if rid:
            self.idempotent[rid] = response
            if rid in self.fail_after_processing:
                self.fail_after_processing.discard(rid)
                raise httpx.ReadTimeout("response lost", request=request)
        return response

    @staticmethod
    def _kind(request: httpx.Request) -> str | None:
        path = request.url.path
        if request.method != "POST":
            return None
        if path == "/v2/checkout/orders":
            return "orders"
        if path.endswith("/authorize"):
            return "authorizations"
        if path.endswith("/capture"):
            return "captures"
        if path.endswith("/void"):
            return "voids"
        return None


def service(fake: FakePayPal) -> PayPalOrdersService:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    client = PayPalClient(client_id=CLIENT_ID, client_secret=SECRET, timeout_seconds=15,
                          http_client=http)
    return PayPalOrdersService(client)


def create_route(fake: FakePayPal, body: dict | None = None, status: int = 201) -> FakePayPal:
    def handler(request: httpx.Request) -> httpx.Response:
        if body is not None:
            return httpx.Response(status, json=body)
        sent = json.loads(request.content)
        money = sent["purchase_units"][0]["amount"]
        return httpx.Response(status, json=order_body(
            money=Money(money["currency_code"], Decimal(money["value"]))))

    return fake.on("POST", "/v2/checkout/orders", handler)


# ====================================================================== create order


def test_create_order_uses_authorize_intent_and_exact_payload():
    fake = create_route(FakePayPal())
    result = run(service(fake).create_order(details()))

    (request,) = fake.api_requests()
    assert request.method == "POST"
    assert str(request.url) == f"{SANDBOX}/v2/checkout/orders"
    assert json.loads(request.content) == {
        "intent": "AUTHORIZE",
        "purchase_units": [{
            "description": "AgentPay Guard payment to ABC Technologies: Software services",
            "amount": {"currency_code": "USD", "value": "250.00"},
        }],
    }
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["prefer"] == "return=representation"
    assert GENERATED_ID.fullmatch(request.headers["paypal-request-id"])
    assert result.order_id == ORDER_ID
    assert result.status == "PAYER_ACTION_REQUIRED"
    assert result.amount == USD_250
    assert result.request_id == request.headers["paypal-request-id"]


def test_intent_is_never_capture():
    source = inspect.getsource(orders_module)
    assert '"intent": "AUTHORIZE"' in source
    assert '"CAPTURE"' not in source and "'CAPTURE'" not in source


@pytest.mark.parametrize(
    ("currency", "amount", "value"),
    [("USD", "49.99", "49.99"), ("EUR", "5", "5.00"), ("GBP", "1000.5", "1000.50"),
     ("CAD", "0.01", "0.01"), ("AUD", "999999.99", "999999.99")],
)
def test_create_order_currency_and_amount(currency, amount, value):
    fake = create_route(FakePayPal())
    result = run(service(fake).create_order(details(currency=currency, amount=amount)))
    sent = json.loads(fake.api_requests()[0].content)["purchase_units"][0]["amount"]
    assert sent == {"currency_code": currency, "value": value}
    assert result.amount == Money(currency, Decimal(value))


def test_create_order_reference_id():
    fake = create_route(FakePayPal())
    run(service(fake).create_order(details(), reference_id="txn_01HZX3K9Q4"))
    unit = json.loads(fake.api_requests()[0].content)["purchase_units"][0]
    assert unit["reference_id"] == unit["custom_id"] == "txn_01HZX3K9Q4"


def test_description_is_clear_and_bounded():
    fake = create_route(FakePayPal())
    run(service(fake).create_order(details(description="Line one\n\nline   two " + "x" * 300)))
    description = json.loads(fake.api_requests()[0].content)["purchase_units"][0]["description"]
    assert description.startswith("AgentPay Guard payment to ABC Technologies: Line one line two")
    assert len(description) == 127 and description.endswith("…")


@pytest.mark.parametrize("rel", ["approve", "payer-action"])
def test_approval_url_extracted(rel):
    links = [{"href": "https://api-m.sandbox.paypal.com/x", "rel": "self", "method": "GET"},
             {"href": APPROVE_URL, "rel": rel, "method": "GET"}]
    fake = create_route(FakePayPal(), order_body(links=links))
    assert run(service(fake).create_order(details())).approval_url == APPROVE_URL


@pytest.mark.parametrize(
    "body",
    [
        {**order_body(), "id": None},
        {**order_body(), "id": "5o190127tn364715t"},
        {**order_body(), "id": "../../v1/oauth2/token"},
        {**order_body(), "status": "COMPLETED"},
        {**order_body(), "status": "APPROVED"},
        {**order_body(), "intent": "CAPTURE"},
        {k: v for k, v in order_body().items() if k != "intent"},
        order_body(money=Money("USD", Decimal("2500.00"))),
        order_body(money=Money("EUR", Decimal("250.00"))),
        {**order_body(), "purchase_units": []},
        {**order_body(), "purchase_units": [order_body()["purchase_units"][0]] * 2},
        {**order_body(), "purchase_units": [{"amount": {"currency_code": "USD", "value": "1e3"}}]},
        order_body(links=[]),
        order_body(links=[{"href": "https://www.paypal.com/checkoutnow?token=X", "rel": "approve"}]),
        order_body(links=[{"href": "https://attacker.example/pay", "rel": "approve"}]),
        order_body(links=[{"href": "http://www.sandbox.paypal.com/checkoutnow", "rel": "approve"}]),
        order_body(links=[{"href": "https://u:p@www.sandbox.paypal.com/x", "rel": "approve"}]),
        order_body(links=[{"href": 42, "rel": "approve"}]),
    ],
)
def test_malformed_create_responses_rejected(body):
    fake = create_route(FakePayPal(), body)
    with pytest.raises(PayPalResponseError):
        run(service(fake).create_order(details()))


@pytest.mark.parametrize("response", [httpx.Response(201, content=b"<html>"),
                                      httpx.Response(201, json=["not", "an", "object"])])
def test_non_object_create_responses_rejected(response):
    fake = FakePayPal().on("POST", "/v2/checkout/orders", response)
    with pytest.raises(PayPalResponseError):
        run(service(fake).create_order(details()))


# ============================================================================ INR


@pytest.mark.parametrize("currency", ["INR", "JPY", "XYZ"])
def test_disallowed_currency_rejected_before_any_http_request(currency):
    fake = create_route(FakePayPal())
    with pytest.raises(PayPalCurrencyNotSupportedError) as exc_info:
        run(service(fake).create_order(details(currency=currency, amount="750000")))
    assert fake.requests == []  # not even an OAuth token request
    assert exc_info.value.status_code == 422
    assert f"{currency} payments cannot be sent to PayPal yet" in str(exc_info.value)
    assert "never converted" in str(exc_info.value)


def test_allowed_currencies_exclude_inr():
    assert PAYPAL_ALLOWED_CURRENCIES == {"USD", "EUR", "GBP", "CAD", "AUD"}


# ===================================================================== retrieve


def test_get_order():
    body = authorized_body()
    body["links"] = [{"href": APPROVE_URL, "rel": "approve", "method": "GET"}]
    fake = FakePayPal().on("GET", f"/v2/checkout/orders/{ORDER_ID}", httpx.Response(200, json=body))
    order = run(service(fake).get_order(ORDER_ID))

    (request,) = fake.api_requests()
    assert request.method == "GET" and "paypal-request-id" not in request.headers
    assert (order.order_id, order.status, order.intent) == (ORDER_ID, "COMPLETED", "AUTHORIZE")
    assert order.amount == USD_250
    assert order.approval_url == APPROVE_URL
    assert [a.authorization_id for a in order.authorizations] == [AUTH_ID]


def test_get_order_without_payments_or_links():
    body = order_body(status="APPROVED", links=[])
    fake = FakePayPal().on("GET", f"/v2/checkout/orders/{ORDER_ID}", httpx.Response(200, json=body))
    order = run(service(fake).get_order(ORDER_ID))
    assert order.authorizations == () and order.approval_url is None


@pytest.mark.parametrize(
    "body",
    [{**order_body(), "id": "OTHERORDERID12345"}, {**order_body(), "intent": "CAPTURE"},
     {**order_body(), "status": "WEIRD"},
     authorized_body(auths=[{**auth_json(), "status": "MYSTERY"}]),
     authorized_body(auths=[{**auth_json(), "id": "bad id"}])],
)
def test_get_order_validates_response(body):
    fake = FakePayPal().on("GET", f"/v2/checkout/orders/{ORDER_ID}", httpx.Response(200, json=body))
    with pytest.raises(PayPalResponseError):
        run(service(fake).get_order(ORDER_ID))


def test_get_unknown_order_maps_not_found():
    fake = FakePayPal()  # no route -> 404 RESOURCE_NOT_FOUND
    with pytest.raises(PayPalRequestRejectedError) as exc_info:
        run(service(fake).get_order(ORDER_ID))
    assert exc_info.value.issue == "RESOURCE_NOT_FOUND"


# ==================================================================== IDs


BAD_IDS = ["", "abc", "5o190127tn364715t", f"{ORDER_ID}/../x", "../../v1/oauth2/token",
           f"{ORDER_ID}?x=1", f"{ORDER_ID} ", "A" * 37, "SHORT1234", None, 12345, ["X"]]


@pytest.mark.parametrize("bad_id", BAD_IDS)
def test_invalid_ids_rejected_before_any_http_request(bad_id):
    fake = FakePayPal()
    svc = service(fake)
    calls = [
        svc.get_order(bad_id),
        svc.authorize_order(bad_id, expected_amount=USD_250),
        svc.capture_authorization(bad_id, expected_amount=USD_250),
        svc.void_authorization(bad_id),
    ]

    async def scenario():
        for call in calls:
            with pytest.raises(PayPalInvalidRequestError):
                await call

    run(scenario())
    assert fake.requests == []


# ====================================================================== authorize


def authorize_route(fake, response):
    return fake.on("POST", f"/v2/checkout/orders/{ORDER_ID}/authorize", response)


def test_authorize_success():
    fake = authorize_route(FakePayPal(), httpx.Response(201, json=authorized_body()))
    result = run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250))

    (request,) = fake.api_requests()
    assert str(request.url) == f"{SANDBOX}/v2/checkout/orders/{ORDER_ID}/authorize"
    assert json.loads(request.content) == {}
    assert request.headers["prefer"] == "return=representation"
    assert GENERATED_ID.fullmatch(request.headers["paypal-request-id"])
    assert result.order_id == ORDER_ID and result.order_status == "COMPLETED"
    assert result.authorization.authorization_id == AUTH_ID
    assert result.authorization.status == "CREATED"
    assert result.authorization.amount == USD_250
    assert result.authorization.expiration_time == "2026-12-11T10:00:00Z"


def test_authorize_pending_is_accepted():
    fake = authorize_route(FakePayPal(), httpx.Response(
        201, json=authorized_body(auths=[auth_json(status="PENDING")])))
    assert run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250)).authorization.status == "PENDING"


@pytest.mark.parametrize("status", ["DENIED", "VOIDED", "EXPIRED", "CAPTURED"])
def test_authorize_unusable_status_is_declined(status):
    fake = authorize_route(FakePayPal(), httpx.Response(
        201, json=authorized_body(auths=[auth_json(status=status)])))
    with pytest.raises(PayPalPaymentDeclinedError):
        run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250))


@pytest.mark.parametrize(
    "body",
    [authorized_body(order_status="APPROVED"),
     authorized_body(auths=[]),
     authorized_body(auths=[auth_json(), auth_json(auth_id="1AB23456789012345")]),
     authorized_body(auths=[auth_json(money=Money("USD", Decimal("2500.00")))]),
     authorized_body(auths=[auth_json(money=Money("EUR", Decimal("250.00")))]),
     {**authorized_body(), "id": "OTHERORDERID12345"},
     order_body(status="COMPLETED")],
)
def test_authorize_malformed_responses(body):
    fake = authorize_route(FakePayPal(), httpx.Response(201, json=body))
    with pytest.raises(PayPalResponseError):
        run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250))


@pytest.mark.parametrize("issue", ["ORDER_NOT_APPROVED", "ORDER_ALREADY_AUTHORIZED", "ORDER_EXPIRED"])
def test_authorize_rejected_by_paypal(caplog, issue):
    caplog.set_level(logging.DEBUG)
    fake = authorize_route(FakePayPal(), httpx.Response(422, json=error_body(issue)))
    with pytest.raises(PayPalRequestRejectedError) as exc_info:
        run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250))
    error = exc_info.value
    assert error.issue == issue and error.upstream_status == 422
    assert_no_secrets(str(error), repr(error), caplog.text)


def test_unrecognised_issue_is_not_surfaced(caplog):
    caplog.set_level(logging.DEBUG)
    fake = authorize_route(FakePayPal(), httpx.Response(422, json=error_body(f"WEIRD_{SENSITIVE}")))
    with pytest.raises(PayPalResponseError) as exc_info:
        run(service(fake).authorize_order(ORDER_ID, expected_amount=USD_250))
    assert not isinstance(exc_info.value, PayPalRequestRejectedError)
    assert_no_secrets(str(exc_info.value), repr(exc_info.value), caplog.text)


# ======================================================================== capture


def capture_route(fake, response):
    return fake.on("POST", f"/v2/payments/authorizations/{AUTH_ID}/capture", response)


def test_capture_authorization_success():
    fake = capture_route(FakePayPal(), httpx.Response(201, json=capture_body()))
    result = run(service(fake).capture_authorization(AUTH_ID, expected_amount=USD_250))

    (request,) = fake.api_requests()
    assert str(request.url) == f"{SANDBOX}/v2/payments/authorizations/{AUTH_ID}/capture"
    assert json.loads(request.content) == {
        "amount": {"currency_code": "USD", "value": "250.00"}, "final_capture": True,
    }
    assert GENERATED_ID.fullmatch(request.headers["paypal-request-id"])
    assert (result.capture_id, result.authorization_id, result.status) == (CAPTURE_ID, AUTH_ID, "COMPLETED")
    assert result.amount == USD_250 and result.final_capture is True


def test_capture_pending_is_accepted():
    fake = capture_route(FakePayPal(), httpx.Response(201, json=capture_body(status="PENDING")))
    assert run(service(fake).capture_authorization(AUTH_ID, expected_amount=USD_250)).status == "PENDING"


@pytest.mark.parametrize("status", ["DECLINED", "FAILED"])
def test_capture_declined(status):
    fake = capture_route(FakePayPal(), httpx.Response(201, json=capture_body(status=status)))
    with pytest.raises(PayPalPaymentDeclinedError):
        run(service(fake).capture_authorization(AUTH_ID, expected_amount=USD_250))


@pytest.mark.parametrize(
    "body",
    [capture_body(money=Money("USD", Decimal("249.99"))),
     capture_body(money=Money("EUR", Decimal("250.00"))),
     capture_body(final_capture=False),
     capture_body(status="UNKNOWN"),
     {**capture_body(), "id": "not-a-paypal-id"},
     {k: v for k, v in capture_body().items() if k != "amount"}],
)
def test_capture_malformed_responses(body):
    fake = capture_route(FakePayPal(), httpx.Response(201, json=body))
    with pytest.raises(PayPalResponseError):
        run(service(fake).capture_authorization(AUTH_ID, expected_amount=USD_250))


@pytest.mark.parametrize(
    "issue", ["AUTHORIZATION_ALREADY_CAPTURED", "AUTHORIZATION_EXPIRED", "AUTHORIZATION_VOIDED"]
)
def test_capture_rejected_by_paypal(issue):
    fake = capture_route(FakePayPal(), httpx.Response(422, json=error_body(issue)))
    with pytest.raises(PayPalRequestRejectedError) as exc_info:
        run(service(fake).capture_authorization(AUTH_ID, expected_amount=USD_250))
    assert exc_info.value.issue == issue


def test_orders_are_never_captured_directly():
    """A full create -> authorize -> capture flow never calls an order capture endpoint."""
    fake = create_route(FakePayPal())
    authorize_route(fake, httpx.Response(201, json=authorized_body()))
    capture_route(fake, httpx.Response(201, json=capture_body()))
    svc = service(fake)

    async def flow():
        order = await svc.create_order(details())
        authorized = await svc.authorize_order(order.order_id, expected_amount=order.amount)
        return await svc.capture_authorization(
            authorized.authorization.authorization_id, expected_amount=order.amount)

    assert run(flow()).status == "COMPLETED"
    paths = [r.url.path for r in fake.api_requests()]
    assert not any(re.fullmatch(r"/v2/checkout/orders/[^/]+/capture", p) for p in paths)
    assert paths[-1] == f"/v2/payments/authorizations/{AUTH_ID}/capture"
    assert not hasattr(PayPalOrdersService, "capture_order")


# =========================================================================== void


def void_route(fake, response):
    return fake.on("POST", f"/v2/payments/authorizations/{AUTH_ID}/void", response)


@pytest.mark.parametrize(
    "response",
    [httpx.Response(204), httpx.Response(200, json=auth_json(status="VOIDED"))],
)
def test_void_success(response):
    fake = void_route(FakePayPal(), response)
    result = run(service(fake).void_authorization(AUTH_ID))
    (request,) = fake.api_requests()
    assert str(request.url) == f"{SANDBOX}/v2/payments/authorizations/{AUTH_ID}/void"
    assert GENERATED_ID.fullmatch(request.headers["paypal-request-id"])
    assert (result.authorization_id, result.status) == (AUTH_ID, "VOIDED")


@pytest.mark.parametrize(
    "body", [auth_json(status="CREATED"), auth_json(auth_id="1AB23456789012345", status="VOIDED"),
             {"status": "VOIDED"}],
)
def test_void_malformed_responses(body):
    fake = void_route(FakePayPal(), httpx.Response(200, json=body))
    with pytest.raises(PayPalResponseError):
        run(service(fake).void_authorization(AUTH_ID))


@pytest.mark.parametrize("issue", ["PREVIOUSLY_VOIDED", "CANNOT_BE_VOIDED", "AUTHORIZATION_ALREADY_CAPTURED"])
def test_void_rejected_by_paypal(issue):
    fake = void_route(FakePayPal(), httpx.Response(422, json=error_body(issue)))
    with pytest.raises(PayPalRequestRejectedError) as exc_info:
        run(service(fake).void_authorization(AUTH_ID))
    assert exc_info.value.issue == issue


# ==================================================================== idempotency


def test_generated_request_ids_are_unique():
    fake = create_route(FakePayPal())
    svc = service(fake)

    async def scenario():
        return [await svc.create_order(details()) for _ in range(3)]

    ids = [r.request_id for r in run(scenario())]
    assert len(set(ids)) == 3
    assert fake.created["orders"] == 3


def test_stable_request_ids_are_sent_for_every_operation():
    fake = create_route(FakePayPal())
    authorize_route(fake, httpx.Response(201, json=authorized_body()))
    capture_route(fake, httpx.Response(201, json=capture_body()))
    void_route(fake, httpx.Response(204))
    svc = service(fake)

    async def scenario():
        await svc.create_order(details(), request_id="txn_1-create")
        await svc.authorize_order(ORDER_ID, expected_amount=USD_250, request_id="txn_1-authorize")
        await svc.capture_authorization(AUTH_ID, expected_amount=USD_250, request_id="txn_1-capture")
        await svc.void_authorization(AUTH_ID, request_id="txn_1-void")

    run(scenario())
    assert [r.headers["paypal-request-id"] for r in fake.api_requests()] == [
        "txn_1-create", "txn_1-authorize", "txn_1-capture", "txn_1-void"]


@pytest.mark.parametrize("bad", ["", "has space", "x" * 109, "a/b", "ünicode", "a\nb", 123])
def test_invalid_request_ids_rejected_before_any_http_request(bad):
    fake = create_route(FakePayPal())
    with pytest.raises(PayPalInvalidRequestError):
        run(service(fake).create_order(details(), request_id=bad))
    assert fake.requests == []


@pytest.mark.parametrize("bad", ["", "has space", "x" * 65, "a/b"])
def test_invalid_reference_ids_rejected_before_any_http_request(bad):
    fake = create_route(FakePayPal())
    with pytest.raises(PayPalInvalidRequestError):
        run(service(fake).create_order(details(), reference_id=bad))
    assert fake.requests == []


def test_retry_with_same_request_id_does_not_create_duplicate_order():
    fake = create_route(FakePayPal())
    fake.fail_after_processing.add("txn_9-create")  # PayPal processes it, response is lost
    svc = service(fake)

    async def scenario():
        with pytest.raises(PayPalTimeoutError):
            await svc.create_order(details(), request_id="txn_9-create")
        return await svc.create_order(details(), request_id="txn_9-create")

    order = run(scenario())
    assert order.order_id == ORDER_ID
    assert fake.created["orders"] == 1
    assert len(fake.api_requests()) == 2


def test_retry_with_same_request_id_does_not_capture_twice():
    fake = capture_route(FakePayPal(), httpx.Response(201, json=capture_body()))
    fake.fail_after_processing.add("txn_9-capture")
    svc = service(fake)

    async def scenario():
        with pytest.raises(PayPalTimeoutError):
            await svc.capture_authorization(AUTH_ID, expected_amount=USD_250, request_id="txn_9-capture")
        first = await svc.capture_authorization(AUTH_ID, expected_amount=USD_250, request_id="txn_9-capture")
        second = await svc.capture_authorization(AUTH_ID, expected_amount=USD_250, request_id="txn_9-capture")
        return first, second

    first, second = run(scenario())
    assert first.capture_id == second.capture_id == CAPTURE_ID
    assert fake.created["captures"] == 1


def test_retry_without_stable_request_id_would_duplicate():
    """Documents why callers must persist and reuse request IDs for retries."""
    fake = create_route(FakePayPal())
    svc = service(fake)

    async def scenario():
        await svc.create_order(details())
        await svc.create_order(details())

    run(scenario())
    assert fake.created["orders"] == 2


# ======================================================================== safety


def test_no_secrets_in_logs_errors_or_results(caplog):
    caplog.set_level(logging.DEBUG)
    fake = create_route(FakePayPal())
    authorize_route(fake, httpx.Response(201, json=authorized_body()))
    capture_route(fake, httpx.Response(422, json=error_body("AUTHORIZATION_EXPIRED")))
    svc = service(fake)

    async def scenario():
        order = await svc.create_order(details())
        authorized = await svc.authorize_order(order.order_id, expected_amount=order.amount)
        with pytest.raises(PayPalRequestRejectedError) as exc_info:
            await svc.capture_authorization(AUTH_ID, expected_amount=order.amount)
        return order, authorized, exc_info.value

    order, authorized, error = run(scenario())
    assert_no_secrets(caplog.text, repr(order), repr(authorized), str(error), repr(error))
    assert "PayPal: order created" in caplog.text


def test_operations_accept_no_risk_decision_or_approval_input():
    forbidden = {"decision", "risk_score", "risk_level", "approval", "approved", "status",
                 "reviewer", "recommendation", "intent"}
    for name in ("create_order", "get_order", "authorize_order", "capture_authorization",
                 "void_authorization"):
        params = set(inspect.signature(getattr(PayPalOrdersService, name)).parameters)
        assert not params & forbidden, (name, params & forbidden)
