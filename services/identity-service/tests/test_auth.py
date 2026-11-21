"""Registration, login and token handling."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import config
from tests.constants import DEFAULT_PASSWORD


async def test_register_creates_farmer_and_hides_password(client):
    response = await client.post(
        "/auth/register",
        json={
            "email": "Sami.Farmer@Example.com",
            "password": DEFAULT_PASSWORD,
            "full_name": "  Sami   Ben Ali ",
            "preferred_language": "aeb",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "sami.farmer@example.com"  # normalized
    assert body["full_name"] == "Sami Ben Ali"
    assert body["role"] == "farmer"
    assert body["preferred_language"] == "aeb"
    assert "password" not in str(body).lower()


async def test_register_ignores_requested_role(client):
    response = await client.post(
        "/auth/register",
        json={"email": "x@example.com", "password": DEFAULT_PASSWORD,
              "full_name": "Sneaky", "role": "admin"},
    )

    assert response.status_code == 201
    assert response.json()["role"] == "farmer"


async def test_register_duplicate_email_is_case_insensitive(client, register):
    await register("dup@example.com")

    response = await client.post(
        "/auth/register",
        json={"email": "DUP@example.com", "password": DEFAULT_PASSWORD, "full_name": "Dup"},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": DEFAULT_PASSWORD, "full_name": "A B"},
        {"email": "a@example.com", "password": "short", "full_name": "A B"},
        {"email": "a@example.com", "password": DEFAULT_PASSWORD, "full_name": "A"},
        {"email": "a@example.com", "password": DEFAULT_PASSWORD, "full_name": "A B",
         "preferred_language": "de"},
    ],
)
async def test_register_rejects_invalid_input(client, payload):
    response = await client.post("/auth/register", json=payload)

    assert response.status_code == 422


async def test_login_returns_valid_token_with_expected_claims(client, register):
    user = await register()

    response = await client.post(
        "/auth/login", json={"email": "FARMER@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == config.settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    claims = jwt.decode(
        body["access_token"],
        config.settings.JWT_SECRET_KEY.get_secret_value(),
        algorithms=["HS256"],
        audience=config.settings.JWT_AUDIENCE,
        issuer=config.settings.JWT_ISSUER,
    )
    assert claims["sub"] == str(user["id"])
    assert claims["role"] == "farmer"
    assert claims["type"] == "access"


async def test_login_errors_are_identical_for_unknown_email_and_wrong_password(client, register):
    await register()

    wrong_password = await client.post(
        "/auth/login", json={"email": "farmer@example.com", "password": "wrong-password"}
    )
    unknown_email = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": DEFAULT_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_inactive_user_cannot_log_in(client, register, login, make_admin):
    await register("admin@example.com")
    await make_admin("admin@example.com")
    user = await register("farmer@example.com")
    admin_headers = await login("admin@example.com")
    await client.patch(f"/users/{user['id']}", json={"is_active": False}, headers=admin_headers)

    response = await client.post(
        "/auth/login", json={"email": "farmer@example.com", "password": DEFAULT_PASSWORD}
    )

    assert response.status_code == 401


def _forge(secret: str | None = None, **overrides) -> str:
    """Build a token for user 1 that is valid unless `overrides` break it."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "1", "role": "admin", "type": "access",
        "iss": config.settings.JWT_ISSUER, "aud": config.settings.JWT_AUDIENCE,
        "iat": now, "exp": now + timedelta(minutes=5),
    }
    payload.update(overrides)
    key = secret or config.settings.JWT_SECRET_KEY.get_secret_value()
    return jwt.encode(payload, key, algorithm="HS256")


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(lambda: "not-a-jwt", id="malformed"),
        pytest.param(lambda: _forge(secret="attacker-secret-attacker-secret-32b"), id="bad-signature"),
        pytest.param(lambda: _forge(exp=datetime.now(timezone.utc) - timedelta(seconds=1)), id="expired"),
        pytest.param(lambda: _forge(aud="another-app"), id="wrong-audience"),
        pytest.param(lambda: _forge(iss="someone-else"), id="wrong-issuer"),
        pytest.param(lambda: _forge(type="refresh"), id="wrong-type"),
        pytest.param(lambda: _forge(sub="999"), id="unknown-user"),
        pytest.param(
            lambda: jwt.encode({"sub": "1"}, key=None, algorithm="none"), id="alg-none"
        ),
    ],
)
async def test_protected_endpoint_rejects_bad_tokens(client, register, token):
    await register()

    response = await client.get("/users/me", headers={"Authorization": f"Bearer {token()}"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_protected_endpoint_requires_token(client):
    response = await client.get("/users/me")

    assert response.status_code == 401
