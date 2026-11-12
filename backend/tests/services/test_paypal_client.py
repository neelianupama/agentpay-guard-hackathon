"""PayPal OAuth client (Step 2). All HTTP is mocked with httpx.MockTransport.

No real or Sandbox network requests are made: an autouse fixture in
tests/conftest.py fails any test that reaches a real httpx transport.
"""

import asyncio
import base64
import logging
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import ConfigurationError, load_settings
from app.main import app
from app.services.paypal import (
    TOKEN_PATH,
    AccessToken,
    PayPalAuthenticationError,
    PayPalClient,
    PayPalError,
    PayPalNotConfiguredError,
    PayPalRateLimitedError,
    PayPalResponseError,
    PayPalTimeoutError,
    PayPalUnavailableError,
)

SANDBOX = "https://api-m.sandbox.paypal.com"
CLIENT_ID = "FAKE-client-id-1234567890"
SECRET = "FAKE-client-secret-0987654321"
TOKEN = "A21AA-FAKE-ACCESS-TOKEN-abcdef"
TOKEN_2 = "A21AA-FAKE-ACCESS-TOKEN-second"
BASIC = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
SENSITIVE_BODY = "SENSITIVE-RAW-PAYPAL-BODY"
FORBIDDEN_TEXT = (CLIENT_ID, SECRET, TOKEN, TOKEN_2, BASIC, SENSITIVE_BODY)


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    """MockTransport handler that records requests and replays responses."""

    def __init__(self, *responses: Callable[[httpx.Request], httpx.Response] | httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return item(request) if callable(item) else item

    @property
    def token_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == TOKEN_PATH]


def token_response(token: str = TOKEN, expires_in: object = 32400, **extra: object) -> httpx.Response:
    body = {"scope": "https://uri.paypal.com/services/payments", "access_token": token,
            "token_type": "Bearer", "app_id": "APP-FAKE", "expires_in": expires_in,
            "nonce": "FAKE-nonce", **extra}
    return httpx.Response(200, json=body)


def make_client(handler, *, clock: Clock | None = None, timeout: float = 15.0):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = PayPalClient(
        client_id=CLIENT_ID,
        client_secret=SECRET,
        timeout_seconds=timeout,
        http_client=http,
        clock=clock or Clock(),
    )
    return client


def run(coro):
    return asyncio.run(coro)


def assert_no_secrets(*texts: str) -> None:
    for text in texts:
        for secret in FORBIDDEN_TEXT:
            assert secret not in text, f"leaked {secret!r}"


# ------------------------------------------------------------------ token acquisition


def test_successful_token_acquisition():
    rec = Recorder(token_response())
    client = make_client(rec)
    assert run(client.get_access_token()) == TOKEN
    assert len(rec.token_requests) == 1


def test_oauth_endpoint_and_grant_type():
    rec = Recorder(token_response())
    run(make_client(rec).get_access_token())

    request = rec.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{SANDBOX}/v1/oauth2/token"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.content.decode()) == {"grant_type": ["client_credentials"]}
    assert request.headers["accept"] == "application/json"


def test_basic_auth_without_exposing_credentials(caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(token_response())
    client = make_client(rec)
    run(client.get_access_token())

    assert rec.requests[0].headers["authorization"] == f"Basic {BASIC}"
    assert SECRET not in rec.requests[0].content.decode()  # not in the form body
    assert_no_secrets(repr(client), str(client), caplog.text)
    assert "PayPal: OAuth access token obtained" in caplog.text


def test_access_token_repr_hides_value():
    token = AccessToken(value=TOKEN, expires_at=1.0, refresh_at=0.5)
    assert TOKEN not in repr(token)


# ------------------------------------------------------------------------ caching


def test_token_reused_before_expiry():
    clock = Clock()
    rec = Recorder(token_response(expires_in=32400))
    client = make_client(rec, clock=clock)

    async def scenario():
        first = await client.get_access_token()
        clock.advance(1000)
        second = await client.get_access_token()
        clock.advance(32400 - 60 - 1000 - 0.001)  # just before the refresh point
        third = await client.get_access_token()
        return first, second, third

    assert run(scenario()) == (TOKEN, TOKEN, TOKEN)
    assert len(rec.token_requests) == 1


def test_token_refreshed_after_expiry():
    clock = Clock()
    rec = Recorder(token_response(TOKEN), token_response(TOKEN_2))
    client = make_client(rec, clock=clock)

    async def scenario():
        first = await client.get_access_token()
        clock.advance(32401)
        return first, await client.get_access_token()

    assert run(scenario()) == (TOKEN, TOKEN_2)
    assert len(rec.token_requests) == 2


def test_token_refreshed_60_seconds_before_expiry():
    clock = Clock()
    rec = Recorder(token_response(TOKEN, expires_in=3600), token_response(TOKEN_2))
    client = make_client(rec, clock=clock)

    async def scenario():
        await client.get_access_token()
        clock.advance(3600 - 61)
        still_cached = await client.get_access_token()
        clock.advance(1)  # now exactly 60 s before expiry
        refreshed = await client.get_access_token()
        return still_cached, refreshed

    assert run(scenario()) == (TOKEN, TOKEN_2)
    assert len(rec.token_requests) == 2


def test_short_lived_token_refreshed_at_half_lifetime():
    clock = Clock()
    rec = Recorder(token_response(TOKEN, expires_in=30), token_response(TOKEN_2))
    client = make_client(rec, clock=clock)

    async def scenario():
        await client.get_access_token()
        clock.advance(14.9)
        reused = await client.get_access_token()
        clock.advance(0.1)  # 15 s = half of a 30 s lifetime
        return reused, await client.get_access_token()

    assert run(scenario()) == (TOKEN, TOKEN_2)


def test_concurrent_calls_share_one_token_request():
    requests: list[httpx.Request] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(0.05)
        return token_response()

    client = make_client(slow_handler)

    async def scenario():
        return await asyncio.gather(*(client.get_access_token() for _ in range(20)))

    tokens = run(scenario())
    assert tokens == [TOKEN] * 20
    assert len(requests) == 1


def test_concurrent_calls_after_expiry_share_one_refresh():
    clock = Clock()
    requests: list[httpx.Request] = []

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(0.02)
        return token_response(TOKEN if len(requests) == 1 else TOKEN_2)

    client = make_client(slow_handler, clock=clock)

    async def scenario():
        await client.get_access_token()
        clock.advance(40000)
        return await asyncio.gather(*(client.get_access_token() for _ in range(10)))

    assert run(scenario()) == [TOKEN_2] * 10
    assert len(requests) == 2


def test_failed_refresh_is_not_cached():
    rec = Recorder(httpx.Response(503), token_response())
    client = make_client(rec)

    async def scenario():
        with pytest.raises(PayPalUnavailableError):
            await client.get_access_token()
        return await client.get_access_token()

    assert run(scenario()) == TOKEN
    assert len(rec.token_requests) == 2


# ------------------------------------------------------------------ HTTP errors


ERROR_BODY = {"error": "invalid_client", "error_description": SENSITIVE_BODY}


@pytest.mark.parametrize(
    ("status", "error_cls", "api_status", "code"),
    [
        (401, PayPalAuthenticationError, 503, "PAYPAL_AUTHENTICATION_FAILED"),
        (403, PayPalAuthenticationError, 503, "PAYPAL_AUTHENTICATION_FAILED"),
        (429, PayPalRateLimitedError, 429, "PAYPAL_RATE_LIMITED"),
        (500, PayPalUnavailableError, 503, "PAYPAL_UNAVAILABLE"),
        (502, PayPalUnavailableError, 503, "PAYPAL_UNAVAILABLE"),
        (503, PayPalUnavailableError, 503, "PAYPAL_UNAVAILABLE"),
        (400, PayPalResponseError, 502, "PAYPAL_BAD_RESPONSE"),
        (404, PayPalResponseError, 502, "PAYPAL_BAD_RESPONSE"),
    ],
)
def test_http_errors_mapped(caplog, status, error_cls, api_status, code):
    caplog.set_level(logging.DEBUG)
    client = make_client(Recorder(httpx.Response(status, json=ERROR_BODY)))

    with pytest.raises(error_cls) as exc_info:
        run(client.get_access_token())

    error = exc_info.value
    assert (error.status_code, error.code, error.upstream_status) == (api_status, code, status)
    assert_no_secrets(str(error), repr(error), caplog.text)
    assert f"status={status}" in caplog.text


def test_redirects_are_not_followed():
    rec = Recorder(httpx.Response(302, headers={"Location": "https://attacker.example/steal"}))
    with pytest.raises(PayPalResponseError):
        run(make_client(rec).get_access_token())
    assert [str(r.url) for r in rec.requests] == [f"{SANDBOX}/v1/oauth2/token"]


# ----------------------------------------------------------- timeouts & connections


@pytest.mark.parametrize(
    ("exc_factory", "error_cls"),
    [
        (lambda r: httpx.ReadTimeout("read timed out", request=r), PayPalTimeoutError),
        (lambda r: httpx.ConnectTimeout("connect timed out", request=r), PayPalTimeoutError),
        (lambda r: httpx.PoolTimeout("pool timed out", request=r), PayPalTimeoutError),
        (lambda r: httpx.ConnectError(f"refused {SECRET}", request=r), PayPalUnavailableError),
        (lambda r: httpx.ReadError("connection reset", request=r), PayPalUnavailableError),
        (lambda r: httpx.RemoteProtocolError("bad framing", request=r), PayPalUnavailableError),
    ],
)
def test_transport_failures(caplog, exc_factory, error_cls):
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_factory(request)

    with pytest.raises(error_cls) as exc_info:
        run(make_client(handler).get_access_token())

    error = exc_info.value
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert_no_secrets(str(error), repr(error), caplog.text)


# ------------------------------------------------------------ malformed responses


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, content=b""),
        httpx.Response(200, json=["access_token", TOKEN]),
        httpx.Response(200, json="token"),
        httpx.Response(200, json={"token_type": "Bearer", "expires_in": 3600}),
        httpx.Response(200, json={"access_token": TOKEN, "token_type": "Bearer"}),
        token_response(token=""),
        token_response(token="   "),
        token_response(token=12345),
        token_response(expires_in=0),
        token_response(expires_in=-5),
        token_response(expires_in="3600"),
        token_response(expires_in=True),
        token_response(expires_in=None),
        token_response(token_type="MAC"),
    ],
)
def test_malformed_token_responses(caplog, response):
    caplog.set_level(logging.DEBUG)
    client = make_client(Recorder(response))
    with pytest.raises(PayPalResponseError) as exc_info:
        run(client.get_access_token())
    assert_no_secrets(str(exc_info.value), repr(exc_info.value), caplog.text, repr(client))
    assert "token_cached=False" in repr(client)


# -------------------------------------------------------------------- timeout config


def test_configured_timeout_applied_to_requests():
    rec = Recorder(token_response())
    run(make_client(rec, timeout=7.5).get_access_token())
    assert rec.requests[0].extensions["timeout"] == {
        "connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5,
    }


@pytest.mark.parametrize(("env_value", "expected"), [(None, 15.0), ("22", 22.0)])
def test_timeout_from_settings(env_value, expected):
    env = {"PAYPAL_CLIENT_ID": CLIENT_ID, "PAYPAL_CLIENT_SECRET": SECRET}
    if env_value is not None:
        env["PAYPAL_TIMEOUT_SECONDS"] = env_value
    rec = Recorder(token_response())
    http = httpx.AsyncClient(transport=httpx.MockTransport(rec))
    client = PayPalClient.from_settings(load_settings(env), http_client=http)

    run(client.get_access_token())

    assert client.timeout == httpx.Timeout(expected)
    assert rec.requests[0].extensions["timeout"]["read"] == expected
    assert client.base_url == SANDBOX


# -------------------------------------------------------------------- construction


@pytest.mark.parametrize(
    "env",
    [{}, {"PAYPAL_CLIENT_ID": CLIENT_ID}, {"PAYPAL_CLIENT_SECRET": SECRET}],
)
def test_missing_credentials_rejected(env):
    with pytest.raises(PayPalNotConfiguredError):
        PayPalClient.from_settings(load_settings(env))


@pytest.mark.parametrize(
    "base_url", ["https://api-m.paypal.com", "https://attacker.example", f"{SANDBOX}/"]
)
def test_client_refuses_non_sandbox_base_url(base_url):
    with pytest.raises(ConfigurationError):
        PayPalClient(client_id=CLIENT_ID, client_secret=SECRET, timeout_seconds=15, base_url=base_url)


def test_injected_http_client_not_closed_but_owned_client_is():
    http = httpx.AsyncClient(transport=httpx.MockTransport(Recorder(token_response())))
    client = PayPalClient(client_id=CLIENT_ID, client_secret=SECRET, timeout_seconds=15, http_client=http)

    async def scenario():
        async with client:
            pass
        assert not http.is_closed
        owned = PayPalClient(client_id=CLIENT_ID, client_secret=SECRET, timeout_seconds=15)
        await owned.aclose()
        assert owned._http.is_closed
        await http.aclose()

    run(scenario())


def test_all_errors_share_base_class():
    for cls in (PayPalAuthenticationError, PayPalNotConfiguredError, PayPalRateLimitedError,
                PayPalResponseError, PayPalTimeoutError, PayPalUnavailableError):
        assert issubclass(cls, PayPalError)
        assert cls.message and cls.code.startswith("PAYPAL_")


# ------------------------------------------------- authenticated request helper


def api_handler(*api_responses: httpx.Response, token_responses=None):
    """Route token requests and API requests separately."""
    tokens = list(token_responses or [token_response(TOKEN), token_response(TOKEN_2)])
    api = list(api_responses)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == TOKEN_PATH:
            return tokens.pop(0) if len(tokens) > 1 else tokens[0]
        return api.pop(0) if len(api) > 1 else api[0]

    return handler, requests


def test_request_sends_bearer_token_and_request_id():
    handler, requests = api_handler(httpx.Response(200, json={"ok": True}))
    client = make_client(handler)

    result = run(client.request("POST", "/v2/example", json={"a": 1}, request_id="req-123"))

    assert result == {"ok": True}
    api_request = requests[-1]
    assert str(api_request.url) == f"{SANDBOX}/v2/example"
    assert api_request.headers["authorization"] == f"Bearer {TOKEN}"
    assert api_request.headers["paypal-request-id"] == "req-123"
    assert api_request.extensions["timeout"]["read"] == 15.0


def test_request_reuses_cached_token():
    handler, requests = api_handler(httpx.Response(200, json={}))
    client = make_client(handler)

    async def scenario():
        await client.request("GET", "/v2/example")
        await client.request("GET", "/v2/example")

    run(scenario())
    assert [r.url.path for r in requests].count(TOKEN_PATH) == 1


def test_request_refreshes_rejected_token_once():
    handler, requests = api_handler(httpx.Response(401), httpx.Response(200, json={"ok": True}))
    client = make_client(handler)

    assert run(client.request("GET", "/v2/example")) == {"ok": True}
    assert [r.url.path for r in requests].count(TOKEN_PATH) == 2
    assert requests[-1].headers["authorization"] == f"Bearer {TOKEN_2}"


def test_request_gives_up_after_second_401(caplog):
    caplog.set_level(logging.DEBUG)
    handler, _ = api_handler(httpx.Response(401, json=ERROR_BODY))
    with pytest.raises(PayPalAuthenticationError) as exc_info:
        run(make_client(handler).request("GET", "/v2/example"))
    assert_no_secrets(str(exc_info.value), caplog.text)


def test_request_empty_body_returns_empty_dict():
    handler, _ = api_handler(httpx.Response(204))
    assert run(make_client(handler).request("POST", "/v2/example")) == {}


@pytest.mark.parametrize(
    ("response", "error_cls"),
    [
        (httpx.Response(422, json=ERROR_BODY), PayPalResponseError),
        (httpx.Response(500, json=ERROR_BODY), PayPalUnavailableError),
        (httpx.Response(429), PayPalRateLimitedError),
        (httpx.Response(200, content=b"<html>"), PayPalResponseError),
    ],
)
def test_request_errors_mapped(caplog, response, error_cls):
    caplog.set_level(logging.DEBUG)
    handler, _ = api_handler(response)
    with pytest.raises(error_cls) as exc_info:
        run(make_client(handler).request("POST", "/v2/example"))
    assert_no_secrets(str(exc_info.value), repr(exc_info.value), caplog.text)


@pytest.mark.parametrize("path", ["v2/example", "https://attacker.example/v2", "//attacker.example"])
def test_request_rejects_non_paypal_paths(path):
    handler, requests = api_handler(httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        run(make_client(handler).request("GET", path))
    assert requests == []


# ----------------------------------------------------------------- frontend exposure


def test_no_api_route_exposes_paypal_credentials_or_tokens():
    from fastapi.testclient import TestClient

    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "paypal" in p.lower() or "oauth" in p.lower() or "token" in p.lower()]
