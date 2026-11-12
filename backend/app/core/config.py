"""Application settings loaded from environment variables.

Values are read from the process environment, optionally populated from
``backend/.env`` via python-dotenv. Copy ``backend/.env.example`` to
``backend/.env`` and fill in values locally; never commit real credentials.

Security rules enforced here:

- PayPal is **Sandbox only**. ``PAYPAL_BASE_URL`` may be empty (defaults to
  Sandbox) or exactly ``https://api-m.sandbox.paypal.com``. Any Live PayPal
  URL or other host raises :class:`ConfigurationError` at startup, so the
  service can never run against Live PayPal by accident.
- Secrets (PayPal client secret, Groq/Elastic keys, reviewer token) are
  excluded from ``repr``/``str`` and never included in error messages.
- A missing or weak ``REVIEWER_TOKEN`` does not stop the service, but
  :func:`require_reviewer_token` refuses protected approval operations until
  a strong token is configured.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_GROQ_TIMEOUT_SECONDS = 20.0

PAYPAL_SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
DEFAULT_PAYPAL_TIMEOUT_SECONDS = 15.0
MIN_PAYPAL_TIMEOUT_SECONDS = 1.0
MAX_PAYPAL_TIMEOUT_SECONDS = 60.0

MIN_REVIEWER_TOKEN_LENGTH = 32

DEFAULT_ELASTIC_INDEX = "agentpay-audit-events"
DEFAULT_ELASTIC_TIMEOUT_SECONDS = 5.0
MIN_ELASTIC_TIMEOUT_SECONDS = 1.0
MAX_ELASTIC_TIMEOUT_SECONDS = 30.0
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ELASTIC_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*/?$")
# Elasticsearch index name rules (lowercase, no leading -_+, limited charset).
_INDEX_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")

# Load backend/.env if present. Existing environment variables take precedence.
load_dotenv(BACKEND_DIR / ".env", override=False)


class ConfigurationError(RuntimeError):
    """Invalid configuration. Messages never contain secret or rejected values."""


class ReviewerTokenNotConfiguredError(ConfigurationError):
    """Protected approval operations are disabled: no usable REVIEWER_TOKEN."""


@dataclass(frozen=True)
class Settings:
    service_name: str
    frontend_url: str

    # Integration settings. Secrets use repr=False so they never appear when a
    # Settings object is printed or logged.
    paypal_client_id: str | None
    paypal_client_secret: str | None = field(repr=False)
    paypal_base_url: str
    groq_api_key: str | None = field(repr=False)
    groq_model: str
    elastic_url: str | None
    elastic_api_key: str | None = field(repr=False)
    groq_timeout_seconds: float = DEFAULT_GROQ_TIMEOUT_SECONDS
    paypal_timeout_seconds: float = DEFAULT_PAYPAL_TIMEOUT_SECONDS
    reviewer_token: str | None = field(default=None, repr=False)
    elastic_index: str = DEFAULT_ELASTIC_INDEX
    elastic_timeout_seconds: float = DEFAULT_ELASTIC_TIMEOUT_SECONDS

    @property
    def cors_origins(self) -> list[str]:
        return [self.frontend_url.rstrip("/")]

    @property
    def paypal_credentials_configured(self) -> bool:
        return bool(self.paypal_client_id and self.paypal_client_secret)

    @property
    def elastic_configured(self) -> bool:
        return bool(self.elastic_url and self.elastic_api_key)

    @property
    def reviewer_token_configured(self) -> bool:
        """True only for a present token of at least MIN_REVIEWER_TOKEN_LENGTH characters."""
        return (
            self.reviewer_token is not None
            and len(self.reviewer_token) >= MIN_REVIEWER_TOKEN_LENGTH
        )


def validate_paypal_base_url(raw: str | None) -> str:
    """Return the Sandbox base URL, or raise for anything else.

    Empty/unset means Sandbox. The rejected value is never echoed, because a
    URL can carry embedded credentials.
    """
    if raw is None or not raw.strip():
        return PAYPAL_SANDBOX_BASE_URL
    if raw.strip() == PAYPAL_SANDBOX_BASE_URL:
        return PAYPAL_SANDBOX_BASE_URL

    lowered = raw.strip().lower()
    is_live = "paypal.com" in lowered and "sandbox" not in lowered
    reason = "Live PayPal is not permitted" if is_live else "this host is not permitted"
    raise ConfigurationError(
        f"Invalid PAYPAL_BASE_URL: {reason}. AgentPay Guard only supports the PayPal "
        f"Sandbox; set PAYPAL_BASE_URL to exactly {PAYPAL_SANDBOX_BASE_URL} or leave it "
        "empty. (The configured value is not shown.)"
    )


def parse_paypal_timeout(raw: str | None) -> float:
    """Seconds for PayPal HTTP calls: default 15, allowed 1-60."""
    if raw is None or not raw.strip():
        return DEFAULT_PAYPAL_TIMEOUT_SECONDS
    message = (
        "Invalid PAYPAL_TIMEOUT_SECONDS: must be a number of seconds between "
        f"{MIN_PAYPAL_TIMEOUT_SECONDS:g} and {MAX_PAYPAL_TIMEOUT_SECONDS:g}, or empty for the "
        f"default of {DEFAULT_PAYPAL_TIMEOUT_SECONDS:g}."
    )
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigurationError(message) from None
    if not math.isfinite(value) or not (
        MIN_PAYPAL_TIMEOUT_SECONDS <= value <= MAX_PAYPAL_TIMEOUT_SECONDS
    ):
        raise ConfigurationError(message)
    return value


def validate_elastic_url(raw: str | None) -> str | None:
    """Empty means "not configured". Otherwise https (http only for localhost),
    no embedded credentials, query or fragment. The value is never echoed."""
    if raw is None or not raw.strip():
        return None
    value = raw.strip()
    message = (
        "Invalid ELASTIC_URL: use the https:// endpoint of your Elasticsearch deployment "
        "(http:// is allowed only for localhost), without credentials, query or fragment. "
        "(The configured value is not shown.)"
    )
    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError:
        raise ConfigurationError(message) from None
    host = (parts.hostname or "").lower()
    if (
        not host
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not _ELASTIC_PATH_RE.fullmatch(parts.path or "/")
        or (port is not None and not 0 < port < 65536)
    ):
        raise ConfigurationError(message)
    if parts.scheme == "https" or (parts.scheme == "http" and host in _LOCAL_HOSTS):
        return value.rstrip("/")
    raise ConfigurationError(message)


def validate_elastic_index(raw: str | None) -> str:
    if raw is None or not raw.strip():
        return DEFAULT_ELASTIC_INDEX
    value = raw.strip()
    if not _INDEX_NAME_RE.fullmatch(value) or ".." in value:
        raise ConfigurationError(
            "Invalid ELASTIC_INDEX: use lowercase letters, digits, '.', '_' or '-' "
            "(max 100 characters, not starting with '-', '_' or '.')."
        )
    return value


def parse_elastic_timeout(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_ELASTIC_TIMEOUT_SECONDS
    message = (
        "Invalid ELASTIC_TIMEOUT_SECONDS: must be a number of seconds between "
        f"{MIN_ELASTIC_TIMEOUT_SECONDS:g} and {MAX_ELASTIC_TIMEOUT_SECONDS:g}."
    )
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigurationError(message) from None
    if not math.isfinite(value) or not (
        MIN_ELASTIC_TIMEOUT_SECONDS <= value <= MAX_ELASTIC_TIMEOUT_SECONDS
    ):
        raise ConfigurationError(message)
    return value


def require_reviewer_token(settings: Settings) -> str:
    """Return the reviewer token, or refuse if it is missing or too weak.

    Every protected approval operation must call this first. The token value
    is never included in the error.
    """
    if settings.reviewer_token is None:
        raise ReviewerTokenNotConfiguredError(
            "Approval operations are disabled: REVIEWER_TOKEN is not set in backend/.env."
        )
    if not settings.reviewer_token_configured:
        raise ReviewerTokenNotConfiguredError(
            "Approval operations are disabled: REVIEWER_TOKEN must be at least "
            f"{MIN_REVIEWER_TOKEN_LENGTH} characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return settings.reviewer_token


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Build and validate settings from an environment mapping."""
    return Settings(
        service_name="agentpay-guard-api",
        frontend_url=environ.get("FRONTEND_URL", "http://localhost:5173"),
        paypal_client_id=_optional(environ, "PAYPAL_CLIENT_ID"),
        paypal_client_secret=_optional(environ, "PAYPAL_CLIENT_SECRET"),
        paypal_base_url=validate_paypal_base_url(environ.get("PAYPAL_BASE_URL")),
        groq_api_key=_optional(environ, "GROQ_API_KEY"),
        groq_model=_optional(environ, "GROQ_MODEL") or DEFAULT_GROQ_MODEL,
        elastic_url=validate_elastic_url(environ.get("ELASTIC_URL")),
        elastic_api_key=_optional(environ, "ELASTIC_API_KEY"),
        paypal_timeout_seconds=parse_paypal_timeout(environ.get("PAYPAL_TIMEOUT_SECONDS")),
        reviewer_token=_optional(environ, "REVIEWER_TOKEN"),
        elastic_index=validate_elastic_index(environ.get("ELASTIC_INDEX")),
        elastic_timeout_seconds=parse_elastic_timeout(environ.get("ELASTIC_TIMEOUT_SECONDS")),
    )


@lru_cache
def get_settings() -> Settings:
    return load_settings(os.environ)
