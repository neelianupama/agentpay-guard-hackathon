"""Asynchronous PayPal Sandbox client with cached OAuth access tokens.

Scope (Step 2): OAuth client-credentials authentication plus a generic,
authenticated ``request`` helper that future Orders operations (create,
authorize, capture, void) will build on. No Orders calls are made here.

Security properties:

- Sandbox only: the base URL is re-validated against the Sandbox-only rule
  from ``app.core.config``; any other URL is refused.
- Credentials and access tokens are never logged, never included in
  exceptions or ``repr``, and never returned to API clients. Logs contain
  only event names and PayPal HTTP status codes.
- Redirects are not followed, so the Authorization header can never be
  forwarded to another host.
- Raw PayPal response bodies are never surfaced; errors are mapped to
  fixed messages (``app.services.paypal.errors``).

Token caching: a token is reused until ``refresh_margin`` seconds before
``expires_in`` elapses. The margin is 60 seconds, or half the token lifetime
if that is shorter, so short-lived tokens are still reused for a while but
always refreshed before they expire. Concurrent callers share one refresh
(an ``asyncio.Lock`` with a re-check after acquiring it).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import Settings, validate_paypal_base_url
from app.services.paypal.errors import (
    PAYPAL_ISSUE_MESSAGES,
    PayPalAuthenticationError,
    PayPalError,
    PayPalNotConfiguredError,
    PayPalRateLimitedError,
    PayPalRequestRejectedError,
    PayPalResponseError,
    PayPalTimeoutError,
    PayPalUnavailableError,
)

logger = logging.getLogger(__name__)

TOKEN_PATH = "/v1/oauth2/token"
DEFAULT_REFRESH_MARGIN_SECONDS = 60.0


@dataclass(frozen=True)
class AccessToken:
    """A cached access token. The value is excluded from ``repr``."""

    value: str = field(repr=False)
    expires_at: float  # clock() time at which PayPal says the token expires
    refresh_at: float  # clock() time after which we fetch a new token


def _refresh_margin(expires_in: float, max_margin: float) -> float:
    """60 s by default, but at most half the lifetime so short tokens are still reused."""
    return min(max_margin, expires_in / 2)


class PayPalClient:
    """PayPal Sandbox REST client.

    Use as an async context manager, or call :meth:`aclose` when done. An
    ``http_client`` can be injected (e.g. with ``httpx.MockTransport`` in
    tests); the client then does not close it. The configured timeout is
    applied to every request either way.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        timeout_seconds: float,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_margin_seconds: float = DEFAULT_REFRESH_MARGIN_SECONDS,
    ) -> None:
        if not client_id or not client_secret:
            raise PayPalNotConfiguredError()
        # Same Sandbox-only rule as configuration; raises ConfigurationError otherwise.
        self._base_url = validate_paypal_base_url(base_url)
        self._auth = httpx.BasicAuth(client_id, client_secret)
        self._timeout = httpx.Timeout(timeout_seconds)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(follow_redirects=False)
        self._clock = clock
        self._refresh_margin = refresh_margin_seconds
        self._token: AccessToken | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(
        cls, settings: Settings, *, http_client: httpx.AsyncClient | None = None
    ) -> PayPalClient:
        if not settings.paypal_credentials_configured:
            raise PayPalNotConfiguredError()
        assert settings.paypal_client_id and settings.paypal_client_secret
        return cls(
            client_id=settings.paypal_client_id,
            client_secret=settings.paypal_client_secret,
            base_url=settings.paypal_base_url,
            timeout_seconds=settings.paypal_timeout_seconds,
            http_client=http_client,
        )

    def __repr__(self) -> str:
        return f"PayPalClient(base_url={self._base_url!r}, token_cached={self._token is not None})"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> httpx.Timeout:
        return self._timeout

    async def __aenter__(self) -> PayPalClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    # ------------------------------------------------------------------ tokens

    async def get_access_token(self) -> str:
        """Return a valid access token, fetching a new one only when needed."""
        token = self._token
        if token is not None and self._clock() < token.refresh_at:
            return token.value
        async with self._lock:
            # Another caller may have refreshed while we waited for the lock.
            token = self._token
            if token is not None and self._clock() < token.refresh_at:
                return token.value
            self._token = await self._fetch_token()
            return self._token.value

    def invalidate_token(self) -> None:
        """Drop the cached token (e.g. after PayPal rejects it)."""
        self._token = None

    async def _fetch_token(self) -> AccessToken:
        logger.info("PayPal: requesting OAuth access token")
        response = await self._send(
            "POST",
            TOKEN_PATH,
            auth=self._auth,
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
        )
        if response.status_code in (401, 403):
            logger.warning("PayPal: OAuth credentials rejected (status=%s)", response.status_code)
            raise PayPalAuthenticationError(upstream_status=response.status_code)
        _raise_for_status(response, operation="OAuth token request")

        body = _json_object(response, operation="OAuth token response")
        value = body.get("access_token")
        expires_in = body.get("expires_in")
        token_type = body.get("token_type", "Bearer")
        if (
            not isinstance(value, str)
            or not value.strip()
            or isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or not expires_in > 0
            or not isinstance(token_type, str)
            or token_type.lower() != "bearer"
        ):
            logger.warning("PayPal: OAuth token response is missing or has invalid fields")
            raise PayPalResponseError(upstream_status=response.status_code)

        now = self._clock()
        lifetime = float(expires_in)
        logger.info("PayPal: OAuth access token obtained (expires_in=%ss)", int(lifetime))
        return AccessToken(
            value=value,
            expires_at=now + lifetime,
            refresh_at=now + lifetime - _refresh_margin(lifetime, self._refresh_margin),
        )

    # --------------------------------------------------------- authenticated calls

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        request_id: str | None = None,
        prefer: str | None = None,
    ) -> dict[str, Any]:
        """Authenticated JSON request to a PayPal API path (for future Orders calls).

        ``request_id`` is sent as ``PayPal-Request-Id`` for idempotent retries.
        ``prefer`` is sent as the ``Prefer`` header (e.g. ``return=representation``).
        If PayPal rejects the cached token (401) it is refreshed once.
        Returns the decoded JSON object (``{}`` for empty bodies).
        """
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("path must be a PayPal API path such as /v2/checkout/orders")

        for attempt in (1, 2):
            token = await self.get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
            if request_id is not None:
                headers["PayPal-Request-Id"] = request_id
            if prefer is not None:
                headers["Prefer"] = prefer
            response = await self._send(method, path, json=json, headers=headers)
            if response.status_code == 401 and attempt == 1:
                logger.info("PayPal: access token rejected; refreshing once")
                self.invalidate_token()
                continue
            if response.status_code in (401, 403):
                logger.warning("PayPal: request not authorized (status=%s)", response.status_code)
                raise PayPalAuthenticationError(upstream_status=response.status_code)
            _raise_for_status(response, operation=f"{method} request")
            if not response.content:
                return {}
            return _json_object(response, operation=f"{method} response")
        raise AssertionError("unreachable")  # pragma: no cover

    # ------------------------------------------------------------------ transport

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            return await self._http.request(
                method, url, timeout=self._timeout, follow_redirects=False, **kwargs
            )
        except httpx.TimeoutException:
            logger.warning("PayPal: request timed out (%s %s)", method, path)
            raise PayPalTimeoutError() from None
        except httpx.TransportError as exc:
            logger.warning("PayPal: connection failed (%s %s): %s", method, path, type(exc).__name__)
            raise PayPalUnavailableError() from None


def _raise_for_status(response: httpx.Response, *, operation: str) -> None:
    status = response.status_code
    if 200 <= status < 300:
        return
    logger.warning("PayPal: %s failed (status=%s)", operation, status)
    if status == 429:
        raise PayPalRateLimitedError(upstream_status=status)
    if status >= 500:
        raise PayPalUnavailableError(upstream_status=status)
    issue = _recognised_issue(response)
    if issue is not None:
        logger.warning("PayPal: %s rejected (issue=%s)", operation, issue)
        raise PayPalRequestRejectedError(issue, upstream_status=status)
    raise PayPalResponseError(upstream_status=status)


def _recognised_issue(response: httpx.Response) -> str | None:
    """PayPal's machine-readable issue code, only if it is on our allowlist."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    candidates: list[object] = []
    details = body.get("details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        candidates.append(details[0].get("issue"))
    candidates.append(body.get("name"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate in PAYPAL_ISSUE_MESSAGES:
            return candidate
    return None


def _json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        logger.warning("PayPal: %s is not valid JSON", operation)
        raise PayPalResponseError(upstream_status=response.status_code) from None
    if not isinstance(body, dict):
        logger.warning("PayPal: %s is not a JSON object", operation)
        raise PayPalResponseError(upstream_status=response.status_code)
    return body


__all__ = ["AccessToken", "PayPalClient", "PayPalError", "TOKEN_PATH"]
