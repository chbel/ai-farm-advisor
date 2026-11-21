from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Weather Service configuration, loaded from environment variables / .env."""

    SERVICE_NAME: str = "weather-service"
    ENVIRONMENT: str = "development"

    # Open-Meteo endpoints. The free tier is for NON-COMMERCIAL use only; for
    # commercial use, point these at the customer endpoints
    # (customer-api.open-meteo.com / customer-archive-api.open-meteo.com) and
    # set OPEN_METEO_API_KEY. No code change is needed.
    OPEN_METEO_FORECAST_URL: AnyHttpUrl = AnyHttpUrl("https://api.open-meteo.com/v1/forecast")
    OPEN_METEO_ARCHIVE_URL: AnyHttpUrl = AnyHttpUrl("https://archive-api.open-meteo.com/v1/archive")
    OPEN_METEO_API_KEY: SecretStr | None = None
    PROVIDER_TIMEOUT_SECONDS: float = 10.0

    # In-memory response cache (per rounded location and request).
    FORECAST_CACHE_TTL_SECONDS: int = 1800
    HISTORY_CACHE_TTL_SECONDS: int = 21600
    CACHE_MAX_ENTRIES: int = 512

    # Used to resolve a farm's location with the caller's own token.
    FARM_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8002")

    # Access tokens are issued by the Identity Service; these must match it.
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "ai-farm-advisor-identity"
    JWT_AUDIENCE: str = "ai-farm-advisor"

    @field_validator("OPEN_METEO_API_KEY", mode="before")
    @classmethod
    def _blank_key_means_none(cls, value):
        # Docker Compose passes unset variables as "" (e.g. ${OPEN_METEO_API_KEY:-}).
        return None if value is None or str(value).strip() == "" else value

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
