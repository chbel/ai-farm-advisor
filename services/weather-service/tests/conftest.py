"""Shared fixtures. Upstreams (Open-Meteo, Farm Service) are simulated with
httpx.MockTransport using payloads shaped like real Open-Meteo responses."""

import os

os.environ["JWT_SECRET_KEY"] = "weather-test-secret-key-at-least-32-bytes"
os.environ["FARM_SERVICE_URL"] = "http://farm.test:8002"
os.environ.pop("OPEN_METEO_API_KEY", None)

from collections.abc import AsyncIterator  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.weather import get_cache, get_http  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.cache import TTLCache  # noqa: E402

START = date(2026, 11, 21)


def make_token(user_id: int = 1, role: str = "farmer", **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(user_id), "role": role, "type": "access",
        "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE,
        "iat": now, "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode(claims, settings.JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


AUTH = {"Authorization": f"Bearer {make_token()}"}


def forecast_payload(days: int = 3, *, tmin=15.5, tmax=23.4, rain=0.0, wind=11.2,
                     hourly_temp=14.0, hourly_rh=60.0, et0=2.44) -> dict:
    dates = [(START + timedelta(days=i)).isoformat() for i in range(days)]
    hours = [f"{d}T{h:02d}:00" for d in dates for h in range(24)]
    per_day = lambda v: v if isinstance(v, list) else [v] * days  # noqa: E731
    return {
        "latitude": 34.712112, "longitude": 10.723164, "elevation": 11.0,
        "timezone": "Africa/Tunis", "utc_offset_seconds": 3600,
        "current": {"time": f"{dates[0]}T09:00", "interval": 900, "temperature_2m": 18.3,
                    "relative_humidity_2m": 52, "precipitation": 0.0,
                    "wind_speed_10m": 5.5, "weather_code": 1},
        "daily": {
            "time": dates,
            "temperature_2m_max": per_day(tmax),
            "temperature_2m_min": per_day(tmin),
            "precipitation_sum": per_day(rain),
            "precipitation_probability_max": per_day(0),
            "et0_fao_evapotranspiration": per_day(et0),
            "wind_speed_10m_max": per_day(wind),
            "relative_humidity_2m_mean": per_day(69),
            "shortwave_radiation_sum": per_day(11.97),
            "weather_code": per_day(3),
        },
        "hourly": {
            "time": hours,
            "temperature_2m": hourly_temp if isinstance(hourly_temp, list) else [hourly_temp] * len(hours),
            "relative_humidity_2m": hourly_rh if isinstance(hourly_rh, list) else [hourly_rh] * len(hours),
        },
    }


def archive_payload(dates: list[str], et0: list) -> dict:
    n = len(dates)
    return {
        "latitude": 34.76274, "longitude": 10.70886, "elevation": 11.0, "timezone": "Africa/Tunis",
        "daily": {
            "time": dates,
            "temperature_2m_max": [28.1] * n, "temperature_2m_min": [21.6] * n,
            "precipitation_sum": [1.0] * n, "et0_fao_evapotranspiration": et0,
            "wind_speed_10m_max": [12.2] * n, "relative_humidity_2m_mean": [62] * n,
            "shortwave_radiation_sum": [17.59] * n, "weather_code": [53] * n,
        },
    }


class FakeUpstreams:
    """Routes requests by host and records them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.forecast = forecast_payload()
        self.archive = archive_payload(["2026-10-01", "2026-10-02"], [3.99, 4.66])
        self.farm_status = 200
        self.farm = {"id": 5, "owner_id": 1, "name": "Domaine Zitouna",
                     "latitude": 34.74, "longitude": 10.76}
        self.provider_error: Exception | None = None
        self.provider_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "farm.test":
            return httpx.Response(self.farm_status, json=self.farm)
        if self.provider_error is not None:
            raise self.provider_error
        if self.provider_status != 200:
            return httpx.Response(self.provider_status, json={"error": True, "reason": "bad"})
        if host == "api.open-meteo.com":
            return httpx.Response(200, json=self.forecast)
        if host == "archive-api.open-meteo.com":
            return httpx.Response(200, json=self.archive)
        raise AssertionError(f"Unexpected host {host}")

    def provider_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host.endswith("open-meteo.com")]


@pytest.fixture
def upstreams() -> FakeUpstreams:
    return FakeUpstreams()


@pytest.fixture
async def client(upstreams) -> AsyncIterator[AsyncClient]:
    mock_http = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))
    cache = TTLCache(max_entries=64)
    app.dependency_overrides[get_http] = lambda: mock_http
    app.dependency_overrides[get_cache] = lambda: cache
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await mock_http.aclose()

