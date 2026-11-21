"""Shared test setup. Runs before the app (and its settings) are imported."""

import os

# Deterministic test values; environment variables take precedence over .env.
os.environ["JWT_SECRET_KEY"] = "gateway-test-secret-key-at-least-32-bytes"
os.environ["IDENTITY_SERVICE_URL"] = "http://identity.test:8001"
os.environ["FARM_SERVICE_URL"] = "http://farm.test:8002"
os.environ["WEATHER_SERVICE_URL"] = "http://weather.test:8003"
os.environ["IRRIGATION_SERVICE_URL"] = "http://irrigation.test:8004"

from collections.abc import Callable, Iterator  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.proxy import get_http_client  # noqa: E402
from app.main import app  # noqa: E402

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def gateway() -> Iterator[Callable[[Handler], TestClient]]:
    """Return a factory that builds a TestClient whose upstream calls hit `handler`."""

    def build(handler: Handler) -> TestClient:
        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.dependency_overrides[get_http_client] = lambda: mock_client
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()
