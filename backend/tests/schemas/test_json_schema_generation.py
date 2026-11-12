"""All API contracts must produce a JSON Schema (used for OpenAPI docs)."""

from app.schemas import (
    ApprovalRequest,
    ErrorResponse,
    PaymentIntent,
    PaymentIntentRequest,
    RiskAssessment,
    Transaction,
)


def test_json_schemas_generate():
    models = (
        PaymentIntentRequest,
        PaymentIntent,
        RiskAssessment,
        ApprovalRequest,
        Transaction,
        ErrorResponse,
    )
    schemas = [model.model_json_schema() for model in models]
    assert len(schemas) == 6
    assert all(schema.get("type") == "object" for schema in schemas)
