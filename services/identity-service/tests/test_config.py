"""Configuration tests: database URL assembly and required secrets."""

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.core.config import Settings

# Isolate tests from the developer's real .env file.
BASE = {"_env_file": None, "JWT_SECRET_KEY": "test-secret"}


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch):
    """Ignore database settings from the process environment (set by conftest)."""
    for name in ("DATABASE_URL", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
                 "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(name, raising=False)


def test_url_is_built_from_parts_and_escapes_special_characters():
    settings = Settings(**BASE, POSTGRES_HOST="postgres", POSTGRES_PORT=5432,
                        POSTGRES_PASSWORD="p@ss:w/rd#1", POSTGRES_DB="identity_db")

    url = make_url(settings.database_url.render_as_string(hide_password=False))

    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database) == ("postgres", 5432, "identity_db")
    assert url.password == "p@ss:w/rd#1"


def test_explicit_database_url_takes_precedence():
    settings = Settings(**BASE, POSTGRES_PASSWORD="unused",
                        DATABASE_URL="postgresql+psycopg://u:p@db:6000/other")

    assert settings.database_url == "postgresql+psycopg://u:p@db:6000/other"


def test_missing_database_credentials_is_rejected():
    with pytest.raises(ValidationError, match="DATABASE_URL or POSTGRES_PASSWORD"):
        Settings(**BASE)


def test_password_is_not_exposed_in_repr():
    settings = Settings(**BASE, POSTGRES_PASSWORD="super-secret")

    assert "super-secret" not in repr(settings)
