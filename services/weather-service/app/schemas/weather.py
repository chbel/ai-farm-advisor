"""Weather Service response models. Units are fixed and documented per field."""

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class DailyWeather(BaseModel):
    """One day of weather at a location (local time zone of the location)."""

    date: date
    temperature_max_c: float | None
    temperature_min_c: float | None
    precipitation_mm: float | None
    precipitation_probability_pct: float | None = Field(
        default=None, description="Forecasts only; null for historical data."
    )
    et0_mm: float | None = Field(
        description="FAO-56 Penman-Monteith reference evapotranspiration, as computed by the provider."
    )
    wind_speed_max_kmh: float | None = Field(description="Max wind speed at 10 m.")
    relative_humidity_mean_pct: float | None
    shortwave_radiation_mj_m2: float | None
    weather_code: int | None = Field(description="WMO weather interpretation code.")
    weather_description: str | None


class DailyIndicators(BaseModel):
    growing_degree_days: float | None = Field(
        description="max(0, (Tmax + Tmin) / 2 - base temperature), °C·day."
    )
    water_balance_mm: float | None = Field(
        description="Precipitation - ET0. Negative = evaporative demand not met by rain."
    )


class DailyWeatherWithIndicators(DailyWeather):
    indicators: DailyIndicators


class PeriodSummary(BaseModel):
    days: int
    total_precipitation_mm: float | None
    total_et0_mm: float | None
    water_balance_mm: float | None
    growing_degree_days: float | None
    base_temperature_c: float
    min_temperature_c: float | None
    max_temperature_c: float | None
    missing_days: int = Field(description="Days with incomplete provider data (excluded).")


class CurrentWeather(BaseModel):
    observed_at: datetime = Field(description="Local time at the location.")
    temperature_c: float | None
    relative_humidity_pct: float | None
    precipitation_mm: float | None
    wind_speed_kmh: float | None
    weather_code: int | None
    weather_description: str | None


class Location(BaseModel):
    latitude: float = Field(description="Grid cell latitude used by the provider.")
    longitude: float = Field(description="Grid cell longitude used by the provider.")
    elevation_m: float | None
    timezone: str


class DataSource(BaseModel):
    provider: str = "Open-Meteo"
    url: str = "https://open-meteo.com/"
    license: str = "CC BY 4.0"
    attribution: str = "Weather data by Open-Meteo.com"
    dataset: str


class RiskLevel(StrEnum):
    MODERATE = "moderate"
    HIGH = "high"


class RiskType(StrEnum):
    FROST = "frost"
    HEAT_STRESS = "heat_stress"
    HEAVY_RAIN = "heavy_rain"
    STRONG_WIND = "strong_wind"
    LATE_BLIGHT_CONDITIONS = "late_blight_conditions"


class RiskAlert(BaseModel):
    type: RiskType
    level: RiskLevel
    date: date
    crops: list[str] = Field(description="Crops this alert is relevant to.")
    message: str
    rule: str = Field(description="The exact rule that triggered the alert.")
    basis: str = Field(description="Where the rule comes from and its limits.")


DISCLAIMER = (
    "Indicators and alerts are rule-based estimates from forecast data. They are "
    "not observations or diagnoses; confirm in the field before acting."
)


class CurrentResponse(BaseModel):
    location: Location
    current: CurrentWeather
    source: DataSource


class ForecastResponse(BaseModel):
    location: Location
    daily: list[DailyWeatherWithIndicators]
    summary: PeriodSummary
    source: DataSource
    disclaimer: str = DISCLAIMER


class HistoryResponse(ForecastResponse):
    pass


class RiskResponse(BaseModel):
    location: Location
    alerts: list[RiskAlert]
    source: DataSource
    disclaimer: str = DISCLAIMER


class FarmRef(BaseModel):
    id: int
    name: str
    latitude: float
    longitude: float


class FarmWeatherResponse(BaseModel):
    farm: FarmRef
    location: Location
    current: CurrentWeather
    daily: list[DailyWeatherWithIndicators]
    summary: PeriodSummary
    alerts: list[RiskAlert]
    source: DataSource
    disclaimer: str = DISCLAIMER
