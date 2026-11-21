"""Weather API endpoints with simulated upstreams."""

from datetime import date

import httpx
import pytest

from tests.conftest import AUTH, forecast_payload, make_token

LOC = {"latitude": 34.7412, "longitude": 10.7637}


async def test_requires_authentication(client):
    assert (await client.get("/weather/current", params=LOC)).status_code == 401
    expired = {"Authorization": f"Bearer {make_token(exp=0)}"}
    assert (await client.get("/weather/current", params=LOC, headers=expired)).status_code == 401


async def test_current_weather(client):
    response = await client.get("/weather/current", params=LOC, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["current"]["temperature_c"] == 18.3
    assert body["current"]["weather_description"] == "Mainly clear"
    assert body["location"]["timezone"] == "Africa/Tunis"
    assert body["source"]["license"] == "CC BY 4.0"


async def test_forecast_requests_expected_variables_and_computes_indicators(client, upstreams):
    response = await client.get("/weather/forecast", params={**LOC, "days": 3}, headers=AUTH)

    assert response.status_code == 200
    call = upstreams.provider_calls()[0]
    assert call.url.params["latitude"] == "34.74"  # rounded for caching
    assert call.url.params["forecast_days"] == "3"
    assert "et0_fao_evapotranspiration" in call.url.params["daily"]
    assert "apikey" not in call.url.params

    body = response.json()
    first = body["daily"][0]
    assert first["et0_mm"] == 2.44
    assert first["indicators"] == {"growing_degree_days": 9.45, "water_balance_mm": -2.44}
    assert body["summary"]["days"] == 3
    assert body["summary"]["total_et0_mm"] == 7.32
    assert body["disclaimer"]


async def test_forecast_is_cached_per_location(client, upstreams):
    for params in (LOC, {"latitude": 34.7399, "longitude": 10.7601}):  # same 0.01° cell
        assert (await client.get("/weather/forecast", params=params, headers=AUTH)).status_code == 200
    assert len(upstreams.provider_calls()) == 1

    await client.get("/weather/forecast", params={"latitude": 36.8, "longitude": 10.18}, headers=AUTH)
    assert len(upstreams.provider_calls()) == 2


@pytest.mark.parametrize(
    "params",
    [{"latitude": 91, "longitude": 10}, {"latitude": 34, "longitude": 181},
     {**LOC, "days": 0}, {**LOC, "days": 17}, {"longitude": 10}],
)
async def test_forecast_validation(client, params):
    assert (await client.get("/weather/forecast", params=params, headers=AUTH)).status_code == 422


async def test_provider_errors_are_mapped(client, upstreams):
    upstreams.provider_status = 400
    response = await client.get("/weather/forecast", params=LOC, headers=AUTH)
    assert response.status_code == 502
    assert "open-meteo.com" not in response.text

    upstreams.provider_status = 200
    upstreams.provider_error = httpx.ReadTimeout("slow")
    assert (await client.get("/weather/current", params=LOC, headers=AUTH)).status_code == 504

    upstreams.provider_error = httpx.ConnectError("down")
    assert (await client.get("/weather/current", params=LOC, headers=AUTH)).status_code == 502


async def test_errors_are_not_cached(client, upstreams):
    upstreams.provider_status = 400
    await client.get("/weather/forecast", params=LOC, headers=AUTH)
    upstreams.provider_status = 200
    assert (await client.get("/weather/forecast", params=LOC, headers=AUTH)).status_code == 200


async def test_history_with_missing_recent_values(client, upstreams):
    upstreams.archive["daily"]["et0_fao_evapotranspiration"] = [3.99, None]

    response = await client.get(
        "/weather/history",
        params={**LOC, "start_date": "2026-10-01", "end_date": "2026-10-02"},
        headers=AUTH,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["daily"][0]["precipitation_probability_pct"] is None
    assert body["summary"]["missing_days"] == 1
    assert body["summary"]["total_et0_mm"] == 3.99
    assert "ERA5" in body["source"]["dataset"]


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-05", "2026-10-01"),  # reversed
        ("1939-12-31", "1940-01-05"),  # before archive
        (date.today().isoformat(), date.today().isoformat()),  # not finished yet
        ("2024-01-01", "2025-01-02"),  # > 366 days
    ],
)
async def test_history_date_validation(client, start, end):
    response = await client.get(
        "/weather/history", params={**LOC, "start_date": start, "end_date": end}, headers=AUTH
    )
    assert response.status_code == 422


async def test_risks_endpoint_reports_frost_and_late_blight(client, upstreams):
    upstreams.forecast = forecast_payload(
        days=2, tmin=[-1.0, 12.0], tmax=[10.0, 20.0], hourly_temp=12.0, hourly_rh=95.0
    )

    body = (await client.get("/weather/risks", params={**LOC, "days": 2}, headers=AUTH)).json()

    kinds = [(a["type"], a["level"], a["date"]) for a in body["alerts"]]
    assert ("frost", "high", "2026-11-21") in kinds
    assert ("late_blight_conditions", "high", "2026-11-22") in kinds
    assert body["disclaimer"]


async def test_farm_weather_uses_farm_location_and_callers_token(client, upstreams):
    response = await client.get("/weather/farms/5", params={"days": 3}, headers=AUTH)

    assert response.status_code == 200
    farm_call = upstreams.requests[0]
    assert str(farm_call.url) == "http://farm.test:8002/farms/5"
    assert farm_call.headers["authorization"] == AUTH["Authorization"]
    assert upstreams.provider_calls()[0].url.params["latitude"] == "34.74"

    body = response.json()
    assert body["farm"]["name"] == "Domaine Zitouna"
    assert len(body["daily"]) == 3 and "alerts" in body and "current" in body


@pytest.mark.parametrize("farm_status,expected", [(404, 404), (401, 401), (500, 502)])
async def test_farm_weather_maps_farm_service_errors(client, upstreams, farm_status, expected):
    upstreams.farm_status = farm_status

    response = await client.get("/weather/farms/5", headers=AUTH)

    assert response.status_code == expected
    assert upstreams.provider_calls() == []


async def test_api_key_is_sent_when_configured(client, upstreams, monkeypatch):
    from pydantic import SecretStr

    from app.core import config

    monkeypatch.setattr(config.settings, "OPEN_METEO_API_KEY", SecretStr("k-123"))
    await client.get("/weather/current", params=LOC, headers=AUTH)

    assert upstreams.provider_calls()[0].url.params["apikey"] == "k-123"


def test_blank_api_key_is_treated_as_unset():
    from app.core.config import Settings

    assert Settings(_env_file=None, OPEN_METEO_API_KEY="").OPEN_METEO_API_KEY is None
    assert Settings(_env_file=None, OPEN_METEO_API_KEY="  ").OPEN_METEO_API_KEY is None
    assert Settings(_env_file=None, OPEN_METEO_API_KEY="k").OPEN_METEO_API_KEY.get_secret_value() == "k"


async def test_health(client):
    assert (await client.get("/health")).json()["status"] == "healthy"
