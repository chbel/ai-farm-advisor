"""Shared fakes. Unit tests need no network, LLM key, model download or database."""

import os
import tempfile

os.environ["JWT_SECRET_KEY"] = "advisor-test-secret-key-at-least-32-bytes"
os.environ.setdefault("POSTGRES_PASSWORD", "unused-in-unit-tests")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="advisor-data-")
os.environ["FARM_SERVICE_URL"] = "http://farm.test:8002"
os.environ["WEATHER_SERVICE_URL"] = "http://weather.test:8003"
os.environ["IRRIGATION_SERVICE_URL"] = "http://irrigation.test:8004"
for key in ("GROQ_API_KEY", "CEREBRAS_API_KEY"):
    os.environ.pop(key, None)

from datetime import datetime, timedelta, timezone  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.knowledge.retriever import Passage  # noqa: E402
from app.llm.client import LLMReply, LLMUnavailable  # noqa: E402


def make_token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": "1", "role": "farmer", "type": "access", "iss": settings.JWT_ISSUER,
              "aud": settings.JWT_AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(overrides)
    return jwt.encode(claims, settings.JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


AUTH = {"Authorization": f"Bearer {make_token()}"}


def passage(n: int = 1, **overrides) -> Passage:
    data = dict(
        source_id=f"src-{n}", title=f"Document {n}", organization="FAO", authors=None, year=2025,
        url=f"https://example.org/{n}", license="CC BY 4.0", commercial_use=True,
        location=f"p. {n * 10}", text=f"Passage {n}: olive trees need regulated deficit irrigation.",
        score=0.9 - n * 0.01,
    )
    data.update(overrides)
    return Passage(**data)


class FakeLLM:
    """Scripted chat model: returns queued replies; records the messages it got."""

    def __init__(self, replies: list[str] | None = None, configured: bool = True,
                 fail: bool = False) -> None:
        self.replies = list(replies or [])
        self._configured = configured
        self.fail = fail
        self.calls: list[list[dict]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def providers(self) -> list:
        return []

    async def chat(self, messages, *, json_mode=False, max_tokens=None) -> LLMReply:
        self.calls.append(messages)
        if self.fail or not self.replies:
            raise LLMUnavailable("scripted failure")
        return LLMReply(content=self.replies.pop(0), provider="fake", model="fake-model")


class FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 384


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def fake_sessionmaker():
    return FakeSession()


FARM = {"id": 5, "name": "Domaine Zitouna", "governorate": "sfax", "latitude": 34.74,
        "longitude": 10.76, "total_area_ha": 10}
FIELDS = [{"id": 3, "name": "Bloc Nord", "area_ha": 3, "soil_texture": "sandy_loam",
           "irrigation_method": "drip"}]
CROPS = [{"id": 10, "crop_type": "olive", "variety": "Chemlali", "status": "active",
          "growth_stage": "pit_hardening", "planting_date": "2010-01-01",
          "days_since_planting": 6000, "plant_count": 300}]
WEATHER = {
    "current": {"temperature_c": 18.0},
    "daily": [{"date": "2026-11-21", "temperature_min_c": 15, "temperature_max_c": 23,
               "precipitation_mm": 0, "et0_mm": 2.4, "wind_speed_max_kmh": 11,
               "precipitation_probability_pct": 0, "weather_description": "Overcast"}],
    "summary": {"total_et0_mm": 2.4},
    "alerts": [{"type": "frost", "level": "high", "date": "2026-11-22", "crops": ["olive"],
                "message": "Frost expected.", "rule": "Tmin <= 0"}],
    "source": {"attribution": "Weather data by Open-Meteo.com"},
}
IRRIGATION = {
    "field_name": "Bloc Nord",
    "recommendations": [{"crop_id": 10, "variety": "Chemlali", "result": {
        "crop_type": "olive", "growth_stage": "pit_hardening",
        "crop_coefficient": {"value": 0.7},
        "totals": {"days": 7, "etc_mm": 11.5, "effective_rain_mm": 0, "net_irrigation_mm": 11.5,
                   "gross_irrigation_mm": 12.8, "gross_volume_m3": 383.0, "litres_per_plant": 1277},
        "schedule": {"interval_days": 1, "advice": "Apply the daily requirement."},
        "limitations": ["Olive Kc assumes 40-60 % ground cover."],
    }}],
    "skipped": [],
}


class Upstreams:
    """Fake Farm / Weather / Irrigation services, recording requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host in self.down:
            raise httpx.ConnectError("down", request=request)
        if host == "weather.test":
            return httpx.Response(200, json=WEATHER)
        if host == "irrigation.test":
            return httpx.Response(200, json=IRRIGATION)
        if path.endswith("/crops"):
            return httpx.Response(200, json=CROPS)
        if path.endswith("/fields"):
            return httpx.Response(200, json=FIELDS)
        if path == "/farms/5":
            return httpx.Response(200, json=FARM)
        return httpx.Response(404, json={"detail": "Farm not found."})
