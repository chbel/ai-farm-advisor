from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Identity Service configuration, loaded from environment variables / .env."""

    SERVICE_NAME: str = "identity-service"
    ENVIRONMENT: str = "development"

    # Database connection, provided as separate parts (the Docker Compose way).
    # Building the URL from parts means passwords never need manual URL-encoding.
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5440
    POSTGRES_USER: str = "farm_admin"
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_DB: str = "identity_db"

    # Optional full SQLAlchemy URL; when set it takes precedence over the parts.
    DATABASE_URL: SecretStr | None = None

    # JWT settings used to sign and verify authentication tokens.
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _require_database_credentials(self) -> "Settings":
        if self.DATABASE_URL is None and self.POSTGRES_PASSWORD is None:
            raise ValueError("Set either DATABASE_URL or POSTGRES_PASSWORD.")
        return self

    @property
    def database_url(self) -> str | URL:
        """SQLAlchemy URL using the async Psycopg 3 driver."""
        if self.DATABASE_URL is not None:
            return self.DATABASE_URL.get_secret_value()
        assert self.POSTGRES_PASSWORD is not None  # guaranteed by the validator
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.POSTGRES_USER,
            password=self.POSTGRES_PASSWORD.get_secret_value(),
            host=self.POSTGRES_HOST,
            port=self.POSTGRES_PORT,
            database=self.POSTGRES_DB,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
