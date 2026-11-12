"""GET /health endpoint, including CORS behaviour."""

from app.schemas import HealthResponse

FRONTEND_ORIGIN = "http://localhost:5173"


def test_health_returns_expected_body(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "agentpay-guard-api"}


def test_health_matches_response_schema(client):
    HealthResponse.model_validate(client.get("/health").json())


def test_health_allows_frontend_origin(client):
    response = client.get("/health", headers={"Origin": FRONTEND_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_cors_preflight_allowed_for_frontend(client):
    response = client.options(
        "/health",
        headers={"Origin": FRONTEND_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND_ORIGIN


def test_cors_preflight_rejected_for_other_origin(client):
    response = client.options(
        "/health",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_health_rejects_post(client):
    assert client.post("/health").status_code == 405
