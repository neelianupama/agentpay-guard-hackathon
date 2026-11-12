"""Secure configuration: Sandbox-only PayPal, PayPal timeout, reviewer token.

All tests build settings from explicit mappings via ``load_settings``; none
read real credentials or make network requests.
"""

import logging
from dataclasses import replace

import pytest

from app.core import config
from app.core.config import (
    DEFAULT_GROQ_MODEL,
    DEFAULT_GROQ_TIMEOUT_SECONDS,
    DEFAULT_PAYPAL_TIMEOUT_SECONDS,
    MIN_REVIEWER_TOKEN_LENGTH,
    PAYPAL_SANDBOX_BASE_URL,
    ConfigurationError,
    ReviewerTokenNotConfiguredError,
    get_settings,
    load_settings,
    require_reviewer_token,
)

FAKE_SECRET = "FAKE-paypal-client-secret-value"
FAKE_TOKEN = "FAKE-reviewer-token-" + "x" * 40
SANDBOX = "https://api-m.sandbox.paypal.com"


def settings_with(**env):
    return load_settings({k: v for k, v in env.items() if v is not None})


# ------------------------------------------------------------------ PayPal base URL


@pytest.mark.parametrize("value", [None, "", "   ", SANDBOX, f"  {SANDBOX}  "])
def test_sandbox_url_accepted(value):
    assert settings_with(PAYPAL_BASE_URL=value).paypal_base_url == SANDBOX


def test_sandbox_constant_is_exact():
    assert PAYPAL_SANDBOX_BASE_URL == "https://api-m.sandbox.paypal.com"


@pytest.mark.parametrize(
    "value",
    [
        "https://api-m.paypal.com",
        "https://api.paypal.com",
        "https://api-m.paypal.com/",
        "HTTPS://API-M.PAYPAL.COM",
        "https://www.paypal.com",
    ],
)
def test_live_url_rejected(value):
    with pytest.raises(ConfigurationError, match="Live PayPal is not permitted") as exc_info:
        settings_with(PAYPAL_BASE_URL=value)
    assert value not in str(exc_info.value)
    assert SANDBOX in str(exc_info.value)  # tells the operator the one allowed value


@pytest.mark.parametrize(
    "value",
    [
        f"{SANDBOX}/",
        f"{SANDBOX}/v2",
        "http://api-m.sandbox.paypal.com",
        "https://api.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com.attacker.example",
        "https://attacker.example/api-m.sandbox.paypal.com",
        "https://user:hunter2@api-m.sandbox.paypal.com",
        "https://localhost:8000",
        "api-m.sandbox.paypal.com",
        "ftp://api-m.sandbox.paypal.com",
    ],
)
def test_other_urls_rejected(value):
    with pytest.raises(ConfigurationError, match="this host is not permitted") as exc_info:
        settings_with(PAYPAL_BASE_URL=value)
    message = str(exc_info.value)
    if value not in f"{SANDBOX}/":  # the allowed URL itself is named in the message
        assert value not in message
    assert "hunter2" not in message


def test_url_error_never_reveals_credentials():
    with pytest.raises(ConfigurationError) as exc_info:
        settings_with(
            PAYPAL_BASE_URL="https://api-m.paypal.com",
            PAYPAL_CLIENT_ID="FAKE-client-id",
            PAYPAL_CLIENT_SECRET=FAKE_SECRET,
            REVIEWER_TOKEN=FAKE_TOKEN,
        )
    message = str(exc_info.value)
    for secret in (FAKE_SECRET, FAKE_TOKEN, "FAKE-client-id"):
        assert secret not in message


def test_invalid_url_stops_startup(monkeypatch):
    """get_settings() (used by create_app) raises, so the API cannot start against Live."""
    monkeypatch.setenv("PAYPAL_BASE_URL", "https://api-m.paypal.com")
    get_settings.cache_clear()
    try:
        with pytest.raises(ConfigurationError):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert get_settings().paypal_base_url == SANDBOX


# ------------------------------------------------------------------ PayPal timeout


@pytest.mark.parametrize("value", [None, "", "  "])
def test_default_timeout_applied(value):
    settings = settings_with(PAYPAL_TIMEOUT_SECONDS=value)
    assert settings.paypal_timeout_seconds == DEFAULT_PAYPAL_TIMEOUT_SECONDS == 15.0


@pytest.mark.parametrize(
    ("value", "expected"), [("30", 30.0), ("1", 1.0), ("60", 60.0), (" 2.5 ", 2.5)]
)
def test_custom_timeout_applied(value, expected):
    assert settings_with(PAYPAL_TIMEOUT_SECONDS=value).paypal_timeout_seconds == expected


@pytest.mark.parametrize("value", ["0", "0.5", "61", "-5", "abc", "nan", "inf", "15s"])
def test_invalid_timeout_rejected(value):
    with pytest.raises(ConfigurationError, match="PAYPAL_TIMEOUT_SECONDS"):
        settings_with(PAYPAL_TIMEOUT_SECONDS=value)


# ------------------------------------------------------------------ reviewer token


def test_reviewer_token_loaded():
    settings = settings_with(REVIEWER_TOKEN=f"  {FAKE_TOKEN}  ")
    assert settings.reviewer_token == FAKE_TOKEN
    assert settings.reviewer_token_configured is True
    assert require_reviewer_token(settings) == FAKE_TOKEN


def test_secrets_not_in_settings_representations():
    settings = settings_with(
        PAYPAL_CLIENT_ID="FAKE-client-id",
        PAYPAL_CLIENT_SECRET=FAKE_SECRET,
        GROQ_API_KEY="FAKE-groq-key",
        ELASTIC_API_KEY="FAKE-elastic-key",
        REVIEWER_TOKEN=FAKE_TOKEN,
    )
    for text in (repr(settings), str(settings), f"{settings}", f"{settings!r}"):
        for secret in (FAKE_SECRET, FAKE_TOKEN, "FAKE-groq-key", "FAKE-elastic-key"):
            assert secret not in text
        assert "reviewer_token" not in text
    assert "paypal_timeout_seconds=15.0" in repr(settings)  # non-secret fields still visible


def test_secrets_not_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    settings = settings_with(PAYPAL_CLIENT_SECRET=FAKE_SECRET, REVIEWER_TOKEN=FAKE_TOKEN)
    logging.getLogger("agentpay.test").info("settings loaded: %s", settings)
    logging.getLogger("agentpay.test").info("settings loaded: %r", settings)
    assert "settings loaded" in caplog.text
    assert FAKE_SECRET not in caplog.text
    assert FAKE_TOKEN not in caplog.text


def test_loading_settings_logs_no_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    settings_with(PAYPAL_CLIENT_SECRET=FAKE_SECRET, REVIEWER_TOKEN=FAKE_TOKEN)
    assert FAKE_SECRET not in caplog.text
    assert FAKE_TOKEN not in caplog.text


@pytest.mark.parametrize("value", [None, "", "    "])
def test_missing_reviewer_token_handled_safely(value):
    settings = settings_with(REVIEWER_TOKEN=value)  # does not stop the service
    assert settings.reviewer_token is None
    assert settings.reviewer_token_configured is False
    with pytest.raises(ReviewerTokenNotConfiguredError, match="REVIEWER_TOKEN is not set"):
        require_reviewer_token(settings)


def test_weak_reviewer_token_disables_approvals_without_revealing_it():
    weak = "short-but-secret"
    settings = settings_with(REVIEWER_TOKEN=weak)
    assert settings.reviewer_token_configured is False
    with pytest.raises(ReviewerTokenNotConfiguredError, match="at least 32 characters") as exc_info:
        require_reviewer_token(settings)
    assert weak not in str(exc_info.value)


def test_token_length_boundary():
    assert settings_with(REVIEWER_TOKEN="a" * MIN_REVIEWER_TOKEN_LENGTH).reviewer_token_configured
    assert not settings_with(
        REVIEWER_TOKEN="a" * (MIN_REVIEWER_TOKEN_LENGTH - 1)
    ).reviewer_token_configured


def test_reviewer_token_error_is_a_configuration_error():
    assert issubclass(ReviewerTokenNotConfiguredError, ConfigurationError)


def test_no_hardcoded_reviewer_token():
    assert settings_with().reviewer_token is None
    with open(config.__file__, encoding="utf-8") as fh:
        source = fh.read()
    assert "REVIEWER_TOKEN=" not in source
    assert "reviewer_token: str | None = field(default=None, repr=False)" in source


# --------------------------------------------------- unchanged existing configuration


def test_groq_and_other_settings_unchanged():
    settings = settings_with(GROQ_API_KEY="FAKE-groq-key", FRONTEND_URL="http://localhost:5173/")
    assert settings.groq_model == DEFAULT_GROQ_MODEL
    assert settings.groq_timeout_seconds == DEFAULT_GROQ_TIMEOUT_SECONDS
    assert settings.groq_api_key == "FAKE-groq-key"
    assert settings.cors_origins == ["http://localhost:5173"]
    assert settings_with(GROQ_MODEL="custom/model").groq_model == "custom/model"


def test_paypal_credentials_configured_flag():
    assert not settings_with().paypal_credentials_configured
    assert not settings_with(PAYPAL_CLIENT_ID="id").paypal_credentials_configured
    assert settings_with(
        PAYPAL_CLIENT_ID="id", PAYPAL_CLIENT_SECRET=FAKE_SECRET
    ).paypal_credentials_configured


def test_current_environment_loads():
    """The real backend/.env satisfies the new rules (secret values are not inspected)."""
    settings = get_settings()
    assert settings.paypal_base_url == SANDBOX
    assert 1 <= settings.paypal_timeout_seconds <= 60
    assert replace(settings) == settings
