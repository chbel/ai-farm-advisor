from datetime import date, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.clients import farm as farm_client
from app.clients.open_meteo import OpenMeteoClient, ProviderError, WeatherData
from app.core.config import settings
from app.core.security import CurrentPrincipal
from app.schemas.weather import (
    CurrentResponse,
    DataSource,
    FarmWeatherResponse,
    ForecastResponse,
    HistoryResponse,
    RiskResponse,
)
from app.services import indicators
from app.services.cache import TTLCache, location_key

router = APIRouter(prefix="/weather", tags=["Weather"])

Latitude = Annotated[float, Query(ge=-90, le=90, description="WGS84 decimal degrees.")]
Longitude = Annotated[float, Query(ge=-180, le=180, description="WGS84 decimal degrees.")]
ForecastDays = Annotated[int, Query(ge=1, le=16, description="Forecast horizon in days.")]
BaseTemperature = Annotated[
    float,
    Query(ge=-5, le=30, description="Base temperature for growing degree days, °C."),
]

MAX_HISTORY_DAYS = 366
EARLIEST_HISTORY = date(1940, 1, 1)

FORECAST_SOURCE = DataSource(dataset="Open-Meteo forecast API (best-match weather models)")
HISTORY_SOURCE = DataSource(dataset="Open-Meteo historical weather API (ERA5 reanalysis)")

_RESPONSES = {
    401: {"description": "Missing or invalid token."},
    502: {"description": "Weather provider error."},
    504: {"description": "Weather provider timeout."},
}


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_cache(request: Request) -> TTLCache:
    return request.app.state.cache


Http = Annotated[httpx.AsyncClient, Depends(get_http)]
Cache = Annotated[TTLCache, Depends(get_cache)]


async def _forecast(http: httpx.AsyncClient, cache: TTLCache, lat: float, lon: float, days: int) -> WeatherData:
    """Cached forecast incl. hourly data (needed for the late blight rule)."""
    lat, lon = location_key(lat, lon)
    try:
        return await cache.get_or_fetch(
            ("forecast", lat, lon, days),
            settings.FORECAST_CACHE_TTL_SECONDS,
            lambda: OpenMeteoClient(http).forecast(lat, lon, days, hourly=True),
        )
    except ProviderError as exc:
        raise _provider_http_error(exc)


def _provider_http_error(exc: ProviderError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT if exc.timeout else status.HTTP_502_BAD_GATEWAY,
        detail=str(exc),
    )


@router.get("/current", response_model=CurrentResponse, responses=_RESPONSES)
async def current_weather(
    _: CurrentPrincipal, http: Http, cache: Cache, latitude: Latitude, longitude: Longitude
) -> CurrentResponse:
    data = await _forecast(http, cache, latitude, longitude, days=1)
    return CurrentResponse(location=data.location, current=data.current, source=FORECAST_SOURCE)


@router.get("/forecast", response_model=ForecastResponse, responses=_RESPONSES)
async def forecast(
    _: CurrentPrincipal,
    http: Http,
    cache: Cache,
    latitude: Latitude,
    longitude: Longitude,
    days: ForecastDays = 7,
    base_temperature: BaseTemperature = indicators.DEFAULT_BASE_TEMPERATURE_C,
) -> ForecastResponse:
    """Daily forecast with ET0, growing degree days and water balance."""
    data = await _forecast(http, cache, latitude, longitude, days)
    daily = indicators.with_indicators(data.daily, base_temperature)
    return ForecastResponse(
        location=data.location,
        daily=daily,
        summary=indicators.summarize(daily, base_temperature),
        source=FORECAST_SOURCE,
    )


@router.get("/history", response_model=HistoryResponse, responses=_RESPONSES)
async def history(
    _: CurrentPrincipal,
    http: Http,
    cache: Cache,
    latitude: Latitude,
    longitude: Longitude,
    start_date: date,
    end_date: date,
    base_temperature: BaseTemperature = indicators.DEFAULT_BASE_TEMPERATURE_C,
) -> HistoryResponse:
    """Observed (reanalysis) daily weather. The most recent ~5 days may be
    incomplete; such days are reported with null values and counted as missing."""

    yesterday = date.today() - timedelta(days=1)
    if not EARLIEST_HISTORY <= start_date <= end_date <= yesterday:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Require {EARLIEST_HISTORY} <= start_date <= end_date <= {yesterday}.",
        )
    if (end_date - start_date).days + 1 > MAX_HISTORY_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"At most {MAX_HISTORY_DAYS} days per request.",
        )

    lat, lon = location_key(latitude, longitude)
    try:
        data = await cache.get_or_fetch(
            ("history", lat, lon, start_date, end_date),
            settings.HISTORY_CACHE_TTL_SECONDS,
            lambda: OpenMeteoClient(http).archive(lat, lon, start_date, end_date),
        )
    except ProviderError as exc:
        raise _provider_http_error(exc)

    daily = indicators.with_indicators(data.daily, base_temperature)
    return HistoryResponse(
        location=data.location,
        daily=daily,
        summary=indicators.summarize(daily, base_temperature),
        source=HISTORY_SOURCE,
    )


@router.get("/risks", response_model=RiskResponse, responses=_RESPONSES)
async def risks(
    _: CurrentPrincipal,
    http: Http,
    cache: Cache,
    latitude: Latitude,
    longitude: Longitude,
    days: ForecastDays = 7,
) -> RiskResponse:
    """Rule-based agricultural weather alerts for the forecast period."""
    data = await _forecast(http, cache, latitude, longitude, days)
    return RiskResponse(
        location=data.location,
        alerts=indicators.risk_alerts(data.daily, data.hourly),
        source=FORECAST_SOURCE,
    )


@router.get(
    "/farms/{farm_id}",
    response_model=FarmWeatherResponse,
    responses={**_RESPONSES, 404: {"description": "Farm not found."}},
)
async def farm_weather(
    farm_id: int,
    request: Request,
    _: CurrentPrincipal,
    http: Http,
    cache: Cache,
    days: ForecastDays = 7,
    base_temperature: BaseTemperature = indicators.DEFAULT_BASE_TEMPERATURE_C,
) -> FarmWeatherResponse:
    """Current weather, forecast with indicators, and alerts at a farm's location."""

    farm = await farm_client.get_farm(http, farm_id, request.headers["authorization"])
    data = await _forecast(http, cache, farm.latitude, farm.longitude, days)
    daily = indicators.with_indicators(data.daily, base_temperature)
    return FarmWeatherResponse(
        farm=farm,
        location=data.location,
        current=data.current,
        daily=daily,
        summary=indicators.summarize(daily, base_temperature),
        alerts=indicators.risk_alerts(data.daily, data.hourly),
        source=FORECAST_SOURCE,
    )
