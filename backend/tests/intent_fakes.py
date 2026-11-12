"""Test doubles for the Groq SDK. No test ever talks to the real provider."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx

from app.services.intent import IntentExtractor

TEST_MODEL = "test-model"
FAKE_SECRET = "gsk_FAKE_SECRET_FOR_LEAK_TESTS"
PROVIDER_URL = "https://api.groq.test/openai/v1/chat/completions"

EXAMPLE_TEXT = "Pay 250 USD to ABC Technologies for software services."


def model_output(**overrides: Any) -> str:
    """JSON the model would return for EXAMPLE_TEXT, with optional overrides."""
    data: dict[str, Any] = {
        "merchant_name": "ABC Technologies",
        "amount": "250",
        "currency": "USD",
        "description": "software services",
        "purpose": None,
        "clarification_needed": True,
    }
    data.update(overrides)
    return json.dumps(data)


def completion(content: Any, finish_reason: str | None = "stop") -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def provider_request() -> httpx.Request:
    return httpx.Request("POST", PROVIDER_URL)


def status_error(error_cls: type, status: int) -> Exception:
    """An SDK status error whose raw message contains a fake secret."""
    response = httpx.Response(status, request=provider_request())
    return error_cls(f"raw provider detail {FAKE_SECRET}", response=response, body=None)


class FakeGroq:
    """Mimics ``groq.Groq().chat.completions.create`` and records every call."""

    def __init__(
        self,
        content: Any = None,
        *,
        error: Exception | None = None,
        response: Any = None,
        finish_reason: str | None = "stop",
    ) -> None:
        self.content = content if content is not None else model_output()
        self.error = error
        self.response = response
        self.finish_reason = finish_reason
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        return completion(self.content, self.finish_reason)

    @property
    def last_messages(self) -> list[dict[str, str]]:
        return self.calls[-1]["messages"]


def make_extractor(fake: FakeGroq) -> IntentExtractor:
    return IntentExtractor(client=fake, model=TEST_MODEL)
