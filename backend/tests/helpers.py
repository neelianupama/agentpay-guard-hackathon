"""Shared assertion helpers for tests."""

from collections.abc import Callable

import pytest
from pydantic import ValidationError


def assert_invalid(build: Callable[[], object], field: str, error_type: str) -> None:
    """Assert that ``build`` raises a ValidationError of ``error_type`` on ``field``."""
    with pytest.raises(ValidationError) as exc_info:
        build()
    errors = [(e["loc"][0] if e["loc"] else "", e["type"]) for e in exc_info.value.errors()]
    assert (field, error_type) in errors, errors
