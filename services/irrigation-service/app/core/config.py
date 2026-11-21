from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Irrigation Service configuration, loaded from environment variables / .env."""

    SERVICE_NAME: str = "irrigation-service"
    ENVIRONMENT: str = "development"

    # Upstream services, called with the caller's own token.
    FARM_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8002")
    WEATHER_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8003")
    UPSTREAM_TIMEOUT_SECONDS: float = 15.0

    # Access tokens are issued by the Identity Service; these must match it.
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "ai-farm-advisor-identity"
    JWT_AUDIENCE: str = "ai-farm-advisor"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
