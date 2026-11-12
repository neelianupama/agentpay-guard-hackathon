"""Reviewer authentication for protected payment actions.

- The token is accepted only from the ``Authorization: Bearer <token>`` header;
  never from request bodies, query strings or URLs.
- Fails closed: if ``REVIEWER_TOKEN`` is missing or too weak, every protected
  action returns 503 and nothing is executed.
- Constant-time comparison (``hmac.compare_digest``).
- The token is never logged, stored on payment records, or returned.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import ReviewerTokenNotConfiguredError, Settings, get_settings, require_reviewer_token

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False, description="Reviewer token from backend REVIEWER_TOKEN.")


class ReviewerAuthError(Exception):
    """Rendered as ``ErrorResponse`` by ``app.api.errors``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None


def require_reviewer(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """FastAPI dependency: allow the request only for the configured reviewer token."""
    try:
        expected = require_reviewer_token(settings)
    except ReviewerTokenNotConfiguredError:
        logger.warning("Reviewer action refused: REVIEWER_TOKEN is not configured")
        raise ReviewerAuthError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REVIEWER_AUTH_NOT_CONFIGURED",
            "Reviewer actions are disabled because reviewer authentication is not configured.",
        ) from None

    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise ReviewerAuthError(
            status.HTTP_401_UNAUTHORIZED, "REVIEWER_AUTH_REQUIRED",
            "Reviewer authentication is required.",
        )
    if not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        logger.warning("Reviewer action refused: invalid reviewer token")
        raise ReviewerAuthError(
            status.HTTP_401_UNAUTHORIZED, "REVIEWER_AUTH_INVALID",
            "Reviewer authentication failed.",
        )


ReviewerRequired = Annotated[None, Depends(require_reviewer)]
