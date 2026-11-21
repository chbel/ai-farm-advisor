"""Test fixtures.

API tests run against a dedicated `identity_db_test` database on the same
PostgreSQL server as development (credentials from .env / environment).
The database is created if missing, migrated with Alembic (so migrations are
tested too), and emptied between tests. Tests are skipped if PostgreSQL is down.
"""

# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import os
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from psycopg import sql
from sqlalchemy.engine import make_url

from app.core import config

TEST_DB_NAME = "identity_db_test"
SERVICE_ROOT = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    url = config.settings.database_url
    url = make_url(url) if isinstance(url, str) else url
    return url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)


# Point the whole app at the test database BEFORE app.core.database is imported
# (it creates the engine at import time). Environment variables take
# precedence over .env in pydantic-settings.
os.environ["DATABASE_URL"] = _test_database_url()
config.get_settings.cache_clear()
config.settings = config.get_settings()

from app.core.database import engine  # noqa: E402
from app.main import app  # noqa: E402


def _admin_conninfo() -> str:
    """Plain libpq connection string to the server's `postgres` maintenance DB."""
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
        await conn.exec_driver_sql("TRUNCATE users RESTART IDENTITY CASCADE")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


from tests.constants import DEFAULT_PASSWORD  # noqa: E402


@pytest.fixture
def register(client: AsyncClient) -> Callable[..., Awaitable[dict]]:
    async def _register(email: str = "farmer@example.com", **overrides) -> dict:
        payload = {"email": email, "password": DEFAULT_PASSWORD, "full_name": "Test Farmer"}
        payload.update(overrides)
        response = await client.post("/auth/register", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return _register


@pytest.fixture
def login(client: AsyncClient) -> Callable[..., Awaitable[dict[str, str]]]:
    """Log in and return ready-to-use Authorization headers."""

    async def _login(email: str = "farmer@example.com", password: str = DEFAULT_PASSWORD):
        response = await client.post("/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _login


@pytest.fixture
def make_admin() -> Callable[[str], Awaitable[None]]:
    async def _make_admin(email: str) -> None:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "UPDATE users SET role = 'admin' WHERE email = %(email)s", {"email": email}
            )

    return _make_admin
