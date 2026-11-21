from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """AI Advisor Service configuration, loaded from environment variables / .env."""

    SERVICE_NAME: str = "ai-advisor-service"
    ENVIRONMENT: str = "development"

    # ---- Database (advisor_db, with the pgvector extension) ----
    POSTGRES_HOST: str = "127.0.0.1"
    POSTGRES_PORT: int = 5440
    POSTGRES_USER: str = "farm_admin"
    POSTGRES_PASSWORD: SecretStr | None = None
    POSTGRES_DB: str = "advisor_db"
    DATABASE_URL: SecretStr | None = None  # optional full URL, overrides the parts

    # ---- LLM providers (OpenAI-compatible APIs serving open-weights models) ----
    # Tried in order; the next one is used when a provider fails or hits its limit.
    GROQ_API_KEY: SecretStr | None = None
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "qwen/qwen3-32b"
    CEREBRAS_API_KEY: SecretStr | None = None
    CEREBRAS_BASE_URL: str = "https://api.cerebras.ai/v1"
    CEREBRAS_MODEL: str = "qwen-3-32b"
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_OUTPUT_TOKENS: int = 1500
    LLM_TEMPERATURE: float = 0.2

    # ---- Knowledge base ----
    # Embedding model: intfloat/multilingual-e5-small (MIT), pinned revision.
    EMBEDDING_REPO: str = "intfloat/multilingual-e5-small"
    EMBEDDING_REVISION: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    DATA_DIR: Path = Path("data")  # model files and downloaded documents
    CHUNK_TOKENS: int = 400
    CHUNK_OVERLAP_TOKENS: int = 60
    RETRIEVAL_TOP_K: int = Field(default=6, ge=1, le=20)
    # Cosine similarity floor. e5 scores are compressed: on this corpus relevant
    # passages scored 0.86-0.93 and an unrelated question ~0.835 (2026-11-21).
    RETRIEVAL_MIN_SCORE: float = Field(default=0.85, ge=0, le=1)
    # Only use sources whose license allows commercial use. Set true in production
    # until licenses for the other sources are obtained.
    KNOWLEDGE_COMMERCIAL_ONLY: bool = False

    # ---- Other services (called with the caller's own token) ----
    FARM_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8002")
    WEATHER_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8003")
    IRRIGATION_SERVICE_URL: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8004")
    UPSTREAM_TIMEOUT_SECONDS: float = 20.0

    # ---- Access tokens (issued by the Identity Service) ----
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

    @field_validator("GROQ_API_KEY", "CEREBRAS_API_KEY", mode="before")
    @classmethod
    def _blank_key_means_none(cls, value):
        # Docker Compose passes unset variables as "".
        return None if value is None or str(value).strip() == "" else value

    @model_validator(mode="after")
    def _require_database_credentials(self) -> "Settings":
        if self.DATABASE_URL is None and self.POSTGRES_PASSWORD is None:
            raise ValueError("Set either DATABASE_URL or POSTGRES_PASSWORD.")
        return self

    @property
    def database_url(self) -> str | URL:
        if self.DATABASE_URL is not None:
            return self.DATABASE_URL.get_secret_value()
        assert self.POSTGRES_PASSWORD is not None
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
