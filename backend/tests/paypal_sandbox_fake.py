"""Stateful in-memory stand-in for the PayPal Sandbox Orders v2 API (tests only).

Used through ``httpx.MockTransport``; no network. It models the lifecycle
(order -> buyer approval -> authorization -> capture/void), honours
``PayPal-Request-Id`` replay like PayPal, and can inject faults per operation:

- ``"lost"``        PayPal processes the request, but the response is lost (timeout)
- ``"timeout"``     timeout before PayPal processes anything
- ``"unavailable"`` HTTP 503, nothing processed
- ``"reject:ISSUE"`` HTTP 422 with that PayPal issue code
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from app.services.paypal import TOKEN_PATH, PayPalClient, PayPalOrdersService

FAKE_CLIENT_ID = "FAKE-paypal-client-id-123"
FAKE_CLIENT_SECRET = "FAKE-paypal-client-secret-456"
FAKE_ACCESS_TOKEN = "A21AA-FAKE-SANDBOX-ACCESS-TOKEN"
SANDBOX = "https://api-m.sandbox.paypal.com"


@dataclass
class FakeOrder:
    order_id: str
    amount: dict
    reference_id: str | None
    status: str = "PAYER_ACTION_REQUIRED"
    authorizations: list[dict] = field(default_factory=list)


class FakeSandbox:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.orders: dict[str, FakeOrder] = {}
        self.authorizations: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.idempotent: dict[str, httpx.Response] = {}
        self.faults: dict[str, list[str]] = defaultdict(list)
        self.counts = {"create": 0, "authorize": 0, "capture": 0, "void": 0}
        self.next_authorization_status = "CREATED"
        self.next_capture_status = "COMPLETED"
        self.order_amount_override: dict | None = None
        self.delay_seconds = 0.0
        self._ids = itertools.count(1)

    # ------------------------------------------------------------- test helpers

    def buyer_approves(self, order_id: str) -> None:
        self.orders[order_id].status = "APPROVED"

    def set_authorization_status(self, authorization_id: str, status: str) -> None:
        self.authorizations[authorization_id]["status"] = status

    def inject(self, operation: str, *kinds: str) -> None:
        self.faults[operation].extend(kinds)

    def api_requests(self, path_suffix: str | None = None) -> list[httpx.Request]:
        out = [r for r in self.requests if r.url.path != TOKEN_PATH]
        if path_suffix:
            out = [r for r in out if r.url.path.endswith(path_suffix)]
        return out

    def service(self) -> PayPalOrdersService:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self))
        client = PayPalClient(client_id=FAKE_CLIENT_ID, client_secret=FAKE_CLIENT_SECRET,
                              timeout_seconds=15, http_client=http)
        return PayPalOrdersService(client)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):014d}"

    # ---------------------------------------------------------------- transport

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == TOKEN_PATH:
            return httpx.Response(200, json={"access_token": FAKE_ACCESS_TOKEN,
                                             "token_type": "Bearer", "expires_in": 32400})
        op = self._operation(request)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        fault = self.faults[op].pop(0) if self.faults[op] else None
        if fault == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if fault == "unavailable":
            return httpx.Response(503, json={"name": "INTERNAL_SERVER_ERROR"})
        if fault and fault.startswith("reject:"):
            return self._error(422, fault.split(":", 1)[1])

        rid = request.headers.get("paypal-request-id")
        if rid and rid in self.idempotent:
            response = self.idempotent[rid]
        else:
            response = self._handle(op, request)
            if rid and response.status_code < 300:
                self.idempotent[rid] = response
        if fault == "lost":
            raise httpx.ReadTimeout("response lost", request=request)
        return response

    @staticmethod
    def _operation(request: httpx.Request) -> str:
        path = request.url.path
        if request.method == "GET":
            return "get"
        if path == "/v2/checkout/orders":
            return "create"
        return path.rsplit("/", 1)[-1]  # authorize | capture | void

    @staticmethod
    def _error(status: int, issue: str) -> httpx.Response:
        return httpx.Response(status, json={
            "name": "UNPROCESSABLE_ENTITY", "message": "FAKE-raw-paypal-message",
            "details": [{"issue": issue, "description": "FAKE-raw-paypal-detail"}]})

    def _order_json(self, order: FakeOrder) -> dict:
        unit: dict = {"reference_id": order.reference_id or "default",
                      "amount": self.order_amount_override or order.amount}
        if order.authorizations:
            unit["payments"] = {"authorizations": [dict(a) for a in order.authorizations]}
        links = [{"href": f"{SANDBOX}/v2/checkout/orders/{order.order_id}", "rel": "self",
                  "method": "GET"}]
        if order.status in ("CREATED", "PAYER_ACTION_REQUIRED"):
            links.append({"href": f"https://www.sandbox.paypal.com/checkoutnow?token={order.order_id}",
                          "rel": "payer-action", "method": "GET"})
        return {"id": order.order_id, "status": order.status, "intent": "AUTHORIZE",
                "purchase_units": [unit], "links": links}

    def _handle(self, op: str, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if op == "create":
            body = json.loads(request.content)
            assert body["intent"] == "AUTHORIZE"
            unit = body["purchase_units"][0]
            order = FakeOrder(self._new_id("ORD"), unit["amount"], unit.get("reference_id"))
            self.orders[order.order_id] = order
            self.counts["create"] += 1
            return httpx.Response(201, json=self._order_json(order))

        match = re.fullmatch(r"/v2/checkout/orders/([A-Z0-9]+)(?:/authorize)?", path)
        if match:
            order = self.orders.get(match.group(1))
            if order is None:
                return self._error(404, "RESOURCE_NOT_FOUND")
            if op == "get":
                for auth in order.authorizations:  # reflect status changes
                    auth.update(self.authorizations[auth["id"]])
                return httpx.Response(200, json=self._order_json(order))
            # authorize
            if order.status == "COMPLETED":
                return self._error(422, "ORDER_ALREADY_AUTHORIZED")
            if order.status != "APPROVED":
                return self._error(422, "ORDER_NOT_APPROVED")
            auth = {"id": self._new_id("AUT"), "status": self.next_authorization_status,
                    "amount": order.amount, "expiration_time": "2026-12-11T10:00:00Z"}
            self.authorizations[auth["id"]] = dict(auth)
            order.authorizations.append(auth)
            order.status = "COMPLETED"
            self.counts["authorize"] += 1
            return httpx.Response(201, json=self._order_json(order))

        match = re.fullmatch(r"/v2/payments/authorizations/([A-Z0-9]+)/(capture|void)", path)
        if match:
            auth = self.authorizations.get(match.group(1))
            if auth is None:
                return self._error(404, "RESOURCE_NOT_FOUND")
            if auth["status"] == "CAPTURED":
                return self._error(422, "AUTHORIZATION_ALREADY_CAPTURED")
            if auth["status"] == "VOIDED":
                return self._error(422, "PREVIOUSLY_VOIDED" if op == "void" else "AUTHORIZATION_VOIDED")
            if op == "capture":
                body = json.loads(request.content)
                assert body["amount"] == auth["amount"] and body["final_capture"] is True
                capture = {"id": self._new_id("CAP"), "status": self.next_capture_status,
                           "amount": body["amount"], "final_capture": True}
                self.captures[capture["id"]] = capture
                if capture["status"] in ("COMPLETED", "PENDING"):
                    auth["status"] = "CAPTURED"
                self.counts["capture"] += 1
                return httpx.Response(201, json=capture)
            auth["status"] = "VOIDED"
            self.counts["void"] += 1
            return httpx.Response(204)

        return self._error(404, "RESOURCE_NOT_FOUND")
