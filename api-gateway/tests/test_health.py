from fastapi.testclient import TestClient

from app.main import app


# TestClient allows us to test the API without starting Uvicorn.
client = TestClient(app)


def test_root_endpoint():
    """Verify that the root endpoint returns the welcome message."""

    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["message"] == "Welcome to AI Farm Advisor"


def test_health_endpoint():
    """Verify that the health endpoint reports a healthy service."""

    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "healthy"
    assert data["service"] == "api-gateway"