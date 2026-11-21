"""Token verification at the gateway: public vs protected routes."""

from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest

from app.core.config import settings

IDENTITY = str(settings.IDENTITY_SERVICE_URL).rstrip("/")


def make_token(secret: str | None = None, **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "7", "role": "farmer", "type": "access",
        "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE,
        "iat": now, "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    key = secret or settings.JWT_SECRET_KEY.get_secret_value()
    return jwt.encode(claims, key, algorithm="HS256")


class Recorder:
    """Fake upstream that records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


@pytest.mark.parametrize("path", ["/api/v1/auth/register", "/api/v1/auth/login"])
def test_public_routes_need_no_token(gateway, path):
    upstream = Recorder()

    response = gateway(upstream).post(path, json={})

    assert response.status_code == 200
    assert len(upstream.requests) == 1


def test_protected_route_without_token_is_rejected_before_upstream(gateway):
    upstream = Recorder()

    response = gateway(upstream).get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert upstream.requests == []


def test_public_path_with_other_method_is_protected(gateway):
    upstream = Recorder()

    response = gateway(upstream).get("/api/v1/auth/login")

    assert response.status_code == 401
    assert upstream.requests == []


def test_valid_token_is_forwarded_to_users_route(gateway):
    upstream = Recorder()
    token = make_token()

    response = gateway(upstream).patch(
        "/api/v1/users/me", json={"full_name": "X Y"}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert str(forwarded.url) == f"{IDENTITY}/users/me"
    assert forwarded.headers["authorization"] == f"Bearer {token}"


@pytest.mark.parametrize(
    "header",
    [
        pytest.param(lambda: "Bearer not-a-jwt", id="malformed"),
        pytest.param(lambda: f"Basic {make_token()}", id="wrong-scheme"),
        pytest.param(lambda: f"Bearer {make_token(secret='x' * 40)}", id="bad-signature"),
        pytest.param(
            lambda: f"Bearer {make_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1))}",
            id="expired",
        ),
        pytest.param(lambda: f"Bearer {make_token(aud='other')}", id="wrong-audience"),
        pytest.param(lambda: f"Bearer {make_token(type='refresh')}", id="wrong-type"),
        pytest.param(
            lambda: "Bearer " + jwt.encode({"sub": "1"}, key=None, algorithm="none"),
            id="alg-none",
        ),
    ],
)
def test_invalid_tokens_are_rejected(gateway, header):
    upstream = Recorder()

    response = gateway(upstream).get("/api/v1/users/me", headers={"Authorization": header()})

    assert response.status_code == 401
    assert response.json()["detail"] in {"Invalid or expired token.", "Not authenticated."}
    assert upstream.requests == []


def test_cors_preflight_is_not_blocked_by_auth(gateway):
    response = gateway(Recorder()).options(
        "/api/v1/users/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_bare_prefix_is_forwarded_without_redirect(gateway):
    upstream = Recorder()

    response = gateway(upstream).get(
        "/api/v1/users?limit=5",
        headers={"Authorization": f"Bearer {make_token()}"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert str(upstream.requests[0].url) == f"{IDENTITY}/users?limit=5"


def test_query_parameter_cannot_spoof_the_forwarded_path(gateway):
    upstream = Recorder()

    response = gateway(upstream).post("/api/v1/auth?path=login", json={})

    assert response.status_code == 401  # bare /auth is not a public route
    assert upstream.requests == []


def test_farm_routes_are_protected_and_forwarded(gateway):
    upstream = Recorder()
    client = gateway(upstream)

    assert client.get("/api/v1/farms").status_code == 401
    assert upstream.requests == []

    response = client.get(
        "/api/v1/farms/3/fields?x=1", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert response.status_code == 200
    assert str(upstream.requests[0].url) == "http://farm.test:8002/farms/3/fields?x=1"


def test_weather_routes_are_protected_and_forwarded(gateway):
    upstream = Recorder()
    client = gateway(upstream)

    assert client.get("/api/v1/weather/forecast?latitude=34&longitude=10").status_code == 401
    assert upstream.requests == []

    response = client.get(
        "/api/v1/weather/farms/5?days=3", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert response.status_code == 200
    assert str(upstream.requests[0].url) == "http://weather.test:8003/weather/farms/5?days=3"


def test_irrigation_routes_are_protected_and_forwarded(gateway):
    upstream = Recorder()
    client = gateway(upstream)

    assert client.post("/api/v1/irrigation/calculate", json={}).status_code == 401
    assert upstream.requests == []

    response = client.get(
        "/api/v1/irrigation/farms/5/fields/3?days=7",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert response.status_code == 200
    assert str(upstream.requests[0].url) == "http://irrigation.test:8004/irrigation/farms/5/fields/3?days=7"


def test_disease_upload_is_protected_and_forwarded_intact(gateway):
    upstream = Recorder()
    client = gateway(upstream)
    files = {"image": ("leaf.jpg", b"fake jpeg bytes", "image/jpeg")}

    assert client.post("/api/v1/disease/analyze", files=files).status_code == 401
    assert upstream.requests == []

    response = client.post("/api/v1/disease/analyze", files=files, data={"crop_type": "tomato"},
                           headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    forwarded = upstream.requests[0]
    assert str(forwarded.url) == "http://disease.test:8005/disease/analyze"
    assert forwarded.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b"fake jpeg bytes" in forwarded.content and b"tomato" in forwarded.content


def test_advisor_routes_are_protected_and_use_longer_timeout(gateway):
    seen = {}

    def upstream(request):
        seen["url"] = str(request.url)
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"answer": "ok"})

    client = gateway(upstream)
    assert client.post("/api/v1/advisor/ask", json={"question": "hi"}).status_code == 401
    assert seen == {}

    response = client.post("/api/v1/advisor/ask", json={"question": "hi"},
                           headers={"Authorization": f"Bearer {make_token()}"})
    assert response.status_code == 200
    assert seen["url"] == "http://advisor.test:8006/advisor/ask"
    assert seen["timeout"]["read"] == 90.0
