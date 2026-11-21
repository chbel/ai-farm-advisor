"""Open-Meteo API client (https://open-meteo.com/en/docs).

Only this module knows the provider's request parameters and response shape;
the rest of the service works with the typed models in app.schemas.weather.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from app.core.config import settings
from app.schemas.weather import CurrentWeather, DailyWeather, Location

logger = logging.getLogger("weather_service.open_meteo")

# Daily variables requested from the provider -> our field names.
_DAILY_FORECAST = {
    "temperature_2m_max": "temperature_max_c",
    "temperature_2m_min": "temperature_min_c",
    "precipitation_sum": "precipitation_mm",
    "precipitation_probability_max": "precipitation_probability_pct",
    "et0_fao_evapotranspiration": "et0_mm",
    "wind_speed_10m_max": "wind_speed_max_kmh",
    "relative_humidity_2m_mean": "relative_humidity_mean_pct",
    "shortwave_radiation_sum": "shortwave_radiation_mj_m2",
    "weather_code": "weather_code",
}
# The archive (reanalysis) has no precipitation probability.
_DAILY_ARCHIVE = {k: v for k, v in _DAILY_FORECAST.items() if k != "precipitation_probability_max"}

_CURRENT = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "relative_humidity_pct",
    "precipitation": "precipitation_mm",
    "wind_speed_10m": "wind_speed_kmh",
    "weather_code": "weather_code",
}
_HOURLY = ("temperature_2m", "relative_humidity_2m")

# WMO weather interpretation codes, as documented by Open-Meteo.
WMO_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


class ProviderError(Exception):
    """The provider could not be reached or rejected the request."""

    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


@dataclass
class HourlySeries:
    time: list[datetime] = field(default_factory=list)
    temperature_c: list[float | None] = field(default_factory=list)
    relative_humidity_pct: list[float | None] = field(default_factory=list)


@dataclass
class WeatherData:
    location: Location
    daily: list[DailyWeather]
    current: CurrentWeather | None = None
    hourly: HourlySeries | None = None


def describe(code: int | None) -> str | None:
    return None if code is None else WMO_CODES.get(code, f"WMO code {code}")


def _as_int(value) -> int | None:
    return None if value is None else int(value)


class OpenMeteoClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def forecast(
        self, latitude: float, longitude: float, days: int, *, hourly: bool = False
    ) -> WeatherData:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "forecast_days": days,
            "daily": ",".join(_DAILY_FORECAST),
            "current": ",".join(_CURRENT),
        }
        if hourly:
            params["hourly"] = ",".join(_HOURLY)
        payload = await self._get(str(settings.OPEN_METEO_FORECAST_URL), params)
        return self._parse(payload, _DAILY_FORECAST, with_current=True, with_hourly=hourly)

    async def archive(
        self, latitude: float, longitude: float, start: date, end: date
    ) -> WeatherData:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": ",".join(_DAILY_ARCHIVE),
        }
        payload = await self._get(str(settings.OPEN_METEO_ARCHIVE_URL), params)
        return self._parse(payload, _DAILY_ARCHIVE, with_current=False, with_hourly=False)

    async def _get(self, url: str, params: dict) -> dict:
        if settings.OPEN_METEO_API_KEY is not None:
            params = {**params, "apikey": settings.OPEN_METEO_API_KEY.get_secret_value()}
        try:
            response = await self._http.get(
                url, params=params, timeout=settings.PROVIDER_TIMEOUT_SECONDS
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("Weather provider timed out.", timeout=True) from exc
        except httpx.RequestError as exc:
            logger.error("Open-Meteo unreachable: %s", type(exc).__name__)
            raise ProviderError("Weather provider is unreachable.") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Open-Meteo returned non-JSON (status %s)", response.status_code)
            raise ProviderError("Weather provider returned an invalid response.") from exc

        if response.status_code != 200 or payload.get("error"):
            # The reason is logged, never the URL (it may contain the API key).
            logger.warning("Open-Meteo error %s: %s", response.status_code, payload.get("reason"))
            raise ProviderError("Weather provider rejected the request.")
        return payload

    @staticmethod
    def _parse(payload: dict, daily_map: dict, *, with_current: bool, with_hourly: bool) -> WeatherData:
        try:
            location = Location(
                latitude=payload["latitude"],
                longitude=payload["longitude"],
                elevation_m=payload.get("elevation"),
                timezone=payload.get("timezone", "UTC"),
            )

            raw = payload["daily"]
            daily = []
            for i, day in enumerate(raw["time"]):
                values = {ours: raw.get(theirs, [None] * len(raw["time"]))[i]
                          for theirs, ours in daily_map.items()}
                values["weather_code"] = _as_int(values.get("weather_code"))
                daily.append(DailyWeather(
                    date=date.fromisoformat(day),
                    weather_description=describe(values["weather_code"]),
                    **values,
                ))

            current = None
            if with_current and "current" in payload:
                cur = payload["current"]
                code = _as_int(cur.get("weather_code"))
                current = CurrentWeather(
                    observed_at=datetime.fromisoformat(cur["time"]),
                    weather_description=describe(code),
                    **{ours: cur.get(theirs) for theirs, ours in _CURRENT.items()} | {"weather_code": code},
                )

            hourly = None
            if with_hourly and "hourly" in payload:
                h = payload["hourly"]
                hourly = HourlySeries(
                    time=[datetime.fromisoformat(t) for t in h["time"]],
                    temperature_c=h["temperature_2m"],
                    relative_humidity_pct=h["relative_humidity_2m"],
                )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("Unexpected Open-Meteo payload: %s", exc)
            raise ProviderError("Weather provider returned an unexpected response.") from exc

        return WeatherData(location=location, daily=daily, current=current, hourly=hourly)
