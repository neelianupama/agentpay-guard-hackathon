"""ErrorResponse validation."""

from app.schemas import ErrorResponse
from tests.helpers import assert_invalid


def test_error_response_minimal():
    assert ErrorResponse(code="NOT_FOUND", message="Missing").model_dump() == {
        "code": "NOT_FOUND",
        "message": "Missing",
        "details": None,
    }


def test_error_response_with_details():
    error = ErrorResponse(code="VALIDATION_ERROR", message="Bad", details={"field": "currency"})
    assert error.details == {"field": "currency"}


def test_lowercase_error_code_rejected():
    assert_invalid(
        lambda: ErrorResponse(code="not_found", message="x"), "code", "string_pattern_mismatch"
    )


def test_empty_message_rejected():
    assert_invalid(lambda: ErrorResponse(code="X", message=""), "message", "string_too_short")
