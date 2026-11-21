"""Calls to the platform services on behalf of the user.

The user's own Authorization header is forwarded, so every service applies its
own access rules: the advisor can never read a farm the user cannot read.
Each function returns compact, LLM-friendly data (not raw API payloads).
"""

import asyncio

import httpx

from app.core.config import settings


class ToolError(Exception):
    """A service call failed; the message is safe to show to the user."""


async def _get(http: httpx.AsyncClient, base, path: str, auth: str, service: str):
    url = f"{str(base).rstrip('/')}{path}"
    try:
        response = await http.get(url, headers={"Authorization": auth},
                                  timeout=settings.UPSTREAM_TIMEOUT_SECONDS)
    except httpx.RequestError:
        raise ToolError(f"{service} is unavailable") from None
    if response.status_code == 404:
        raise ToolError(f"{service}: not found or not accessible")
    if response.status_code != 200:
        raise ToolError(f"{service} returned HTTP {response.status_code}")
    return response.json()


async def farm_context(http: httpx.AsyncClient, auth: str, farm_id: int, max_fields: int = 5) -> dict:
    farm = await _get(http, settings.FARM_SERVICE_URL, f"/farms/{farm_id}", auth, "farm-service")
    fields = await _get(http, settings.FARM_SERVICE_URL, f"/farms/{farm_id}/fields", auth, "farm-service")
    fields = fields[:max_fields]
    crops = await asyncio.gather(*(
        _get(http, settings.FARM_SERVICE_URL, f"/farms/{farm_id}/fields/{f['id']}/crops", auth, "farm-service")
        for f in fields
    ))
    return {
        "farm": {k: farm.get(k) for k in ("id", "name", "governorate", "latitude", "longitude", "total_area_ha")},
        "fields": [
            {
                **{k: f.get(k) for k in ("id", "name", "area_ha", "soil_texture", "irrigation_method")},
                "crops": [
                    {k: c.get(k) for k in ("id", "crop_type", "variety", "status", "growth_stage",
                                           "planting_date", "days_since_planting", "plant_count")}
                    for c in field_crops
                ],
            }
            for f, field_crops in zip(fields, crops)
        ],
    }


async def weather_context(http: httpx.AsyncClient, auth: str, farm_id: int, days: int = 7) -> dict:
    data = await _get(http, settings.WEATHER_SERVICE_URL, f"/weather/farms/{farm_id}?days={days}",
                      auth, "weather-service")
    return {
        "current": data.get("current"),
        "daily": [
            {k: d.get(k) for k in ("date", "temperature_min_c", "temperature_max_c", "precipitation_mm",
                                   "precipitation_probability_pct", "et0_mm", "wind_speed_max_kmh",
                                   "weather_description")}
            for d in data.get("daily", [])
        ],
        "summary": data.get("summary"),
        "alerts": [{k: a.get(k) for k in ("type", "level", "date", "crops", "message", "rule")}
                   for a in data.get("alerts", [])],
        "source": (data.get("source") or {}).get("attribution"),
    }


async def irrigation_context(http: httpx.AsyncClient, auth: str, farm_id: int,
                             field_id: int | None, days: int = 7, max_fields: int = 3) -> dict:
    if field_id is not None:
        field_ids = [field_id]
    else:
        fields = await _get(http, settings.FARM_SERVICE_URL, f"/farms/{farm_id}/fields", auth, "farm-service")
        field_ids = [f["id"] for f in fields if f.get("irrigation_method") != "rainfed"][:max_fields]
    results = await asyncio.gather(*(
        _get(http, settings.IRRIGATION_SERVICE_URL, f"/irrigation/farms/{farm_id}/fields/{fid}?days={days}",
             auth, "irrigation-service")
        for fid in field_ids
    ))
    out = []
    for rec in results:
        for item in rec.get("recommendations", []):
            r = item["result"]
            out.append({
                "field": rec.get("field_name"),
                "crop": r["crop_type"], "variety": item.get("variety"), "growth_stage": r["growth_stage"],
                "kc": r["crop_coefficient"]["value"],
                "totals": {k: r["totals"].get(k) for k in ("days", "etc_mm", "effective_rain_mm",
                                                           "net_irrigation_mm", "gross_irrigation_mm",
                                                           "gross_volume_m3", "litres_per_plant")},
                "schedule": r["schedule"],
                "limitations": r.get("limitations", []),
            })
        for skipped in rec.get("skipped", []):
            out.append({"field": rec.get("field_name"), "crop": skipped["crop_type"],
                        "skipped": skipped["reason"]})
    return {"method": "FAO-56 (ETc = Kc x ET0)", "fields": out}


def disease_context(analysis: dict) -> dict:
    """Keep only what the composer needs from a /disease/analyze result."""
    top = analysis.get("top_prediction") or {}
    return {
        "crop": analysis.get("crop"),
        "outcome": analysis.get("outcome"),
        "top": {k: top.get(k) for k in ("label", "name_en", "name_fr", "causal_agent", "probability")},
        "candidates": [{k: c.get(k) for k in ("name_en", "probability")}
                       for c in analysis.get("candidates", [])],
        "model_field_test_accuracy": (analysis.get("model") or {}).get("field_test_accuracy"),
    }
