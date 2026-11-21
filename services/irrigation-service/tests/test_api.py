"""API tests. Farm and Weather services are simulated with httpx.MockTransport."""

import os

os.environ["JWT_SECRET_KEY"] = "irrigation-test-secret-key-at-least-32-bytes"
os.environ["FARM_SERVICE_URL"] = "http://farm.test:8002"
os.environ["WEATHER_SERVICE_URL"] = "http://weather.test:8003"

from datetime import datetime, timedelta, timezone  # noqa: E402

import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.irrigation import get_http  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def token(**overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": "1", "role": "farmer", "type": "access", "iss": settings.JWT_ISSUER,
              "aud": settings.JWT_AUDIENCE, "iat": now, "exp": now + timedelta(minutes=5)}
    claims.update(overrides)
    return jwt.encode(claims, settings.JWT_SECRET_KEY.get_secret_value(), algorithm="HS256")


AUTH = {"Authorization": f"Bearer {token()}"}

FIELD = {"id": 3, "farm_id": 5, "name": "Parcelle Nord", "area_ha": 2.0,
         "soil_texture": "sandy_loam", "irrigation_method": "drip"}
CROPS = [
    {"id": 10, "crop_type": "olive", "variety": "Chemlali", "status": "active",
     "growth_stage": "pit_hardening", "plant_count": 200},
    {"id": 11, "crop_type": "tomato", "variety": None, "status": "active",
     "growth_stage": None, "plant_count": None},
    {"id": 12, "crop_type": "tomato", "variety": None, "status": "harvested",
     "growth_stage": "late_season", "plant_count": None},
]


def weather_payload(days: int = 3) -> dict:
    return {
        "daily": [{"date": f"2026-07-0{i + 1}", "et0_mm": 6.0, "precipitation_mm": 0.0}
                  for i in range(days)],
        "source": {"attribution": "Weather data by Open-Meteo.com",
                   "dataset": "Open-Meteo forecast API (best-match weather models)"},
    }


class Upstreams:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = {"farm.test": 200, "weather.test": 200}
        self.down: set[str] = set()
        self.crops = CROPS
        self.weather = weather_payload()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host in self.down:
            raise httpx.ConnectError("down", request=request)
        if self.status[host] != 200:
            return httpx.Response(self.status[host], json={"detail": "x"})
        if host == "weather.test":
            return httpx.Response(200, json=self.weather)
        if request.url.path.endswith("/crops"):
            return httpx.Response(200, json=self.crops)
        return httpx.Response(200, json=FIELD)


@pytest.fixture
def upstreams() -> Upstreams:
    return Upstreams()


@pytest.fixture
async def client(upstreams):
    mock = httpx.AsyncClient(transport=httpx.MockTransport(upstreams))
    app.dependency_overrides[get_http] = lambda: mock
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await mock.aclose()


CALC = {
    "crop_type": "tomato", "growth_stage": "mid_season", "area_ha": 1.0,
    "irrigation_method": "surface", "soil_texture": "loam",
    "weather": [{"date": "2026-07-01", "et0_mm": 6.0}, {"date": "2026-07-02", "et0_mm": 6.0}],
}


async def test_requires_authentication(client):
    assert (await client.post("/irrigation/calculate", json=CALC)).status_code == 401
    expired = {"Authorization": f"Bearer {token(exp=0)}"}
    assert (await client.get("/irrigation/reference", headers=expired)).status_code == 401


async def test_calculate_endpoint(client):
    response = await client.post("/irrigation/calculate", json=CALC, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["net_irrigation_mm"] == 13.8  # 2 x 1.15 x 6
    assert body["totals"]["gross_irrigation_mm"] == 23.0  # / 0.60 (surface)
    assert body["assumptions"] and body["sources"]


async def test_calculate_rejects_invalid_stage(client):
    bad = {**CALC, "growth_stage": "pit_hardening"}
    response = await client.post("/irrigation/calculate", json=bad, headers=AUTH)
    assert response.status_code == 422


async def test_reference_lists_parameters_and_sources(client):
    body = (await client.get("/irrigation/reference", headers=AUTH)).json()

    assert body["crops"]["tomato"]["kc_mid"] == 1.15
    assert body["application_efficiency"]["drip"] == 0.9
    assert "clay_loam" not in body["soils"]
    assert body["stage_positions"]["olive"]["pit_hardening"] == "mid"


async def test_field_recommendation(client, upstreams):
    response = await client.get("/irrigation/farms/5/fields/3?days=3", headers=AUTH)

    assert response.status_code == 200
    urls = [str(r.url) for r in upstreams.requests]
    assert urls == [
        "http://farm.test:8002/farms/5/fields/3",
        "http://farm.test:8002/farms/5/fields/3/crops",
        "http://weather.test:8003/weather/farms/5?days=3",
    ]
    assert all(r.headers["authorization"] == AUTH["Authorization"] for r in upstreams.requests)

    body = response.json()
    assert body["field_name"] == "Parcelle Nord"
    assert [r["crop_id"] for r in body["recommendations"]] == [10]
    olive = body["recommendations"][0]["result"]
    assert olive["crop_coefficient"]["value"] == 0.70
    assert olive["totals"]["net_irrigation_mm"] == 12.6  # 3 days x 0.70 x 6
    assert olive["schedule"]["interval_days"] == 1  # drip field

    reasons = {s["crop_id"]: s["reason"] for s in body["skipped"]}
    assert "No growth stage" in reasons[11]
    assert "harvested" in reasons[12]
    # Two crops are active on the field (one without stage): volumes caveat is added.
    assert any("Several active crops" in item for item in olive["limitations"])
    assert "Open-Meteo" in body["weather_source"]


async def test_field_recommendation_drops_days_without_et0(client, upstreams):
    upstreams.weather["daily"][1]["et0_mm"] = None

    body = (await client.get("/irrigation/farms/5/fields/3?days=3", headers=AUTH)).json()

    assert body["forecast_days"] == 2
    assert any("no ET0" in item for item in body["recommendations"][0]["result"]["limitations"])


@pytest.mark.parametrize(
    "host,status,expected",
    [("farm.test", 404, 404), ("farm.test", 401, 401), ("weather.test", 500, 502)],
)
async def test_upstream_errors_are_mapped(client, upstreams, host, status, expected):
    upstreams.status[host] = status
    response = await client.get("/irrigation/farms/5/fields/3", headers=AUTH)
    assert response.status_code == expected


async def test_weather_service_down_returns_503(client, upstreams):
    upstreams.down.add("weather.test")
    response = await client.get("/irrigation/farms/5/fields/3", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "weather-service is currently unavailable."
