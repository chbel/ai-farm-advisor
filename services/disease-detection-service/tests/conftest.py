"""Test setup: a temporary model directory with a tiny tomato model."""

import os
import tempfile
from pathlib import Path

MODEL_ROOT = Path(tempfile.mkdtemp(prefix="disease-models-"))
os.environ["MODEL_DIR"] = str(MODEL_ROOT)
os.environ["JWT_SECRET_KEY"] = "disease-test-secret-key-at-least-32-bytes"

from tests.model_factory import write_model_folder  # noqa: E402

write_model_folder(MODEL_ROOT / "tomato")

import io  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from PIL import Image  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.model import load_models  # noqa: E402


def make_token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": "1", "role": "farmer", "type": "access", "iss": settings.JWT_ISSUER,
              "aud": settings.JWT_AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(overrides)
    return jwt.encode(claims, settings.JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


AUTH = {"Authorization": f"Bearer {make_token()}"}


def image_bytes(color=(200, 30, 30), size=(400, 300), fmt="JPEG", **save_args) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt, **save_args)
    return buffer.getvalue()


@pytest.fixture
async def client():
    # Load models explicitly: ASGITransport does not run the lifespan.
    app.state.models = load_models(MODEL_ROOT, threads=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
