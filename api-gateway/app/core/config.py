from functools import lru_cache

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration for the API Gateway.

    BaseSettings automatically reads values from environment
    variables and the .env file.
    """

    # Basic application information
    APP_NAME: str = "AI Farm Advisor API Gateway"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"

    # Only frontend applications from these origins can access our API.
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    # Internal base URLs of the microservices the gateway forwards to.
    # Defaults match running the services locally with uvicorn.
    IDENTITY_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8001")

    # Upper bound for a single upstream call, in seconds.
    UPSTREAM_TIMEOUT_SECONDS: float = 15.0

    # JWT verification. Must match the Identity Service settings.
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "ai-farm-advisor-identity"
    JWT_AUDIENCE: str = "ai-farm-advisor"

    # Tell Pydantic how to load and validate configuration.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Create the settings object once and reuse it.

    This avoids repeatedly loading configuration
    whenever another module requests application settings.
    """
    return Settings()


# Shared settings instance used throughout the application.
settings = get_settings()
