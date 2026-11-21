"""Gateway routing tests. Upstreams are simulated with httpx.MockTransport."""

import json

import httpx

from app.core.config import settings

IDENTITY = str(settings.IDENTITY_SERVICE_URL).rstrip("/")


def test_forwards_method_path_query_body_and_headers(gateway):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = request.headers
        return httpx.Response(201, json={"id": 1}, headers={"x-custom": "kept"})

    response = gateway(handler).post(
        "/api/v1/auth/register?lang=fr",
        json={"email": "a@b.tn"},
        headers={"Authorization": "Bearer token123", "X-Request-ID": "req-42"},
    )

    assert response.status_code == 201
    assert response.json() == {"id": 1}
    assert response.headers["x-custom"] == "kept"
    assert response.headers["x-request-id"] == "req-42"

    assert seen["method"] == "POST"
    assert seen["url"] == f"{IDENTITY}/auth/register?lang=fr"
    assert seen["body"] == {"email": "a@b.tn"}
    assert seen["headers"]["authorization"] == "Bearer token123"
    assert seen["headers"]["x-request-id"] == "req-42"
    # The gateway's own Host header must not leak upstream.
    assert seen["headers"]["host"] != "testserver"


def test_generates_request_id_when_missing(gateway):
    response = gateway(lambda r: httpx.Response(200, json={})).post("/api/v1/auth/login", json={})

    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) == 36  # UUID4


def test_relays_upstream_error_status_unchanged(gateway):
    handler = lambda r: httpx.Response(401, json={"detail": "Incorrect email or password."})

    response = gateway(handler).post("/api/v1/auth/login", json={})

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password."


def test_upstream_unreachable_returns_503_without_internal_details(gateway):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.5:8001", request=request)

    response = gateway(handler).post("/api/v1/auth/login", json={})

    assert response.status_code == 503
    assert response.json() == {"detail": "identity-service is currently unavailable."}
    assert "10.0.0.5" not in response.text


def test_upstream_timeout_returns_504(gateway):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    response = gateway(handler).post("/api/v1/auth/login", json={})

    assert response.status_code == 504


def test_unknown_route_returns_404(gateway):
    response = gateway(lambda r: httpx.Response(200)).get("/api/v1/unknown/thing")

    assert response.status_code == 404


def test_readiness_reports_each_service(gateway):
    ok = gateway(lambda r: httpx.Response(200, json={"status": "healthy"}))
    assert ok.get("/health/ready").json() == {
        "status": "ready",
        "services": {
            "identity-service": "healthy",
            "farm-service": "healthy",
            "weather-service": "healthy",
            "irrigation-service": "healthy",
            "disease-detection-service": "healthy",
            "ai-advisor-service": "healthy",
        },
    }

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    response = gateway(down).get("/health/ready")
    assert response.status_code == 503
    assert response.json()["services"] == {
        "identity-service": "unavailable",
        "farm-service": "unavailable",
        "weather-service": "unavailable",
        "irrigation-service": "unavailable",
        "disease-detection-service": "unavailable",
        "ai-advisor-service": "unavailable",
    }
