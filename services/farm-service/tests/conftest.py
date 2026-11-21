"""Test fixtures.

API tests run against a dedicated `farm_db_test` database on the development
PostgreSQL server. It is created if missing, migrated with Alembic, and emptied
between tests. Tests are skipped if PostgreSQL is not reachable.
"""

# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import os
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core import config

TEST_DB_NAME = "farm_db_test"
SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    url = config.settings.database_url
    url = make_url(url) if isinstance(url, str) else url
    return url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)


# Point the app at the test database BEFORE app.core.database creates its engine.
os.environ["DATABASE_URL"] = _test_database_url()
config.get_settings.cache_clear()
config.settings = config.get_settings()

from app.core.database import engine  # noqa: E402
from app.main import app  # noqa: E402


def _admin_conninfo() -> str:
    url = make_url(os.environ["DATABASE_URL"])
    return psycopg.conninfo.make_conninfo(
        host=url.host, port=url.port, user=url.username,
        password=url.password, dbname="postgres", connect_timeout=3,
    )


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> None:
    try:
        with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,)
            ).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME)))
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not reachable; start it with `docker compose up -d postgres`.")

    alembic_cfg = Config(str(SERVICE_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(SERVICE_ROOT / "alembic"))
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(autouse=True)
async def _clean_tables() -> AsyncIterator[None]:
    yield
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE farm_activities, crop_stage_events, crops, fields, farms "
            "RESTART IDENTITY CASCADE"
        )


def make_token(user_id: int = 1, role: str = "farmer", **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id), "role": role, "type": "access",
        "iss": config.settings.JWT_ISSUER, "aud": config.settings.JWT_AUDIENCE,
        "iat": now, "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, config.settings.JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


@pytest.fixture
def auth() -> Callable[..., dict[str, str]]:
    """auth(user_id, role) -> Authorization headers."""
    return lambda user_id=1, role="farmer": {"Authorization": f"Bearer {make_token(user_id, role)}"}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


FARM = {"name": "Domaine Zitouna", "governorate": "sfax",
        "latitude": 34.74, "longitude": 10.76, "total_area_ha": 10}
FIELD = {"name": "Parcelle Nord", "area_ha": 4, "soil_texture": "sandy_loam",
         "irrigation_method": "drip"}


@pytest.fixture
def create_farm(client, auth):
    async def _create(user_id: int = 1, **overrides) -> dict:
        response = await client.post("/farms", json={**FARM, **overrides}, headers=auth(user_id))
        assert response.status_code == 201, response.text
        return response.json()
    return _create


@pytest.fixture
def create_field(client, auth):
    async def _create(farm_id: int, user_id: int = 1, **overrides) -> dict:
        response = await client.post(
            f"/farms/{farm_id}/fields", json={**FIELD, **overrides}, headers=auth(user_id)
        )
        assert response.status_code == 201, response.text
        return response.json()
    return _create
