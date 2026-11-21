from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Disease Detection Service configuration (environment variables / .env)."""

    SERVICE_NAME: str = "disease-detection-service"
    ENVIRONMENT: str = "development"

    # One sub-folder per crop, each with model.onnx + metadata.json
    # (produced by ml/disease-detection/train_tomato.py).
    MODEL_DIR: Path = Path("models")
    ONNX_THREADS: int = Field(default=2, ge=1, le=16)

    # Upload validation.
    MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024
    MIN_IMAGE_SIDE_PX: int = 128  # smaller photos are too blurry to classify
    MAX_IMAGE_PIXELS: int = 40_000_000  # decompression-bomb guard (~40 MP)

    # A prediction is "uncertain" below this calibrated probability, or when the
    # top two classes are closer than the margin.
    MIN_CONFIDENCE: float = Field(default=0.60, ge=0, le=1)
    MIN_MARGIN: float = Field(default=0.15, ge=0, le=1)

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
