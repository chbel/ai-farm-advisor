"""Irrigation Service request/response models. Depths in mm, volumes in m3."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CropType(StrEnum):
    OLIVE = "olive"
    TOMATO = "tomato"


class IrrigationMethod(StrEnum):
    DRIP = "drip"
    SPRINKLER = "sprinkler"
    SURFACE = "surface"
    RAINFED = "rainfed"


class DailyWeatherInput(BaseModel):
    date: date
    et0_mm: float = Field(ge=0, le=20, description="Reference evapotranspiration (FAO-56).")
    precipitation_mm: float = Field(default=0.0, ge=0, le=500)


class ClimateInput(BaseModel):
    """Optional inputs for the FAO-56 Eq. 62 Kc climate adjustment."""

    wind_speed_2m_ms: float = Field(ge=0, le=20, description="Mean wind speed at 2 m, m/s.")
    rh_min_pct: float = Field(ge=0, le=100, description="Mean daily minimum relative humidity, %.")


class CalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop_type: CropType
    growth_stage: str
    stage_progress: float = Field(
        default=0.5, ge=0, le=1,
        description="Position inside the development/late stage (0 = start, 1 = end).",
    )
    area_ha: float = Field(gt=0, le=100_000)
    irrigation_method: IrrigationMethod
    soil_texture: str = "unknown"
    plant_count: int | None = Field(default=None, gt=0)
    root_depth_m: float | None = Field(
        default=None, gt=0, le=5, description="Override the FAO-56 Table 22 default."
    )
    effective_rain_fraction: float = Field(default=0.8, ge=0, le=1)
    climate: ClimateInput | None = None
    weather: list[DailyWeatherInput] = Field(min_length=1, max_length=31)

    @model_validator(mode="after")
    def _consistent(self):
        # Imported here to keep schemas free of calculation code at import time.
        from app.services.fao56 import STAGE_POSITIONS

        stages = STAGE_POSITIONS[self.crop_type.value]
        if self.growth_stage not in stages:
            raise ValueError(
                f"Stage '{self.growth_stage}' is not valid for {self.crop_type.value}. "
                f"Allowed: {', '.join(stages)}"
            )
        dates = [d.date for d in self.weather]
        if len(set(dates)) != len(dates):
            raise ValueError("Weather days must have unique dates.")
        self.weather.sort(key=lambda d: d.date)
        return self


class CropCoefficient(BaseModel):
    value: float
    curve_position: str = Field(description="initial, development, mid or late (FAO-56 Fig. 25).")
    kc_ini: float
    kc_mid: float
    kc_end: float
    climate_adjusted: bool


class DailyRequirement(BaseModel):
    date: date
    et0_mm: float
    etc_mm: float
    precipitation_mm: float
    effective_rain_mm: float
    deficit_mm: float = Field(description="ETc - effective rain (negative = rain surplus).")


class Totals(BaseModel):
    days: int
    etc_mm: float
    effective_rain_mm: float
    net_irrigation_mm: float = Field(description="Water the crop needs from irrigation.")
    application_efficiency: float | None
    gross_irrigation_mm: float | None = Field(description="Water to apply, including losses.")
    gross_volume_m3: float | None
    litres_per_plant: float | None


class SoilWaterStatus(BaseModel):
    available: bool
    reason: str | None = None
    root_depth_m: float
    depletion_fraction_p: float | None = None
    total_available_water_mm: float | None = None
    readily_available_water_mm: float | None = None


class Schedule(BaseModel):
    interval_days: int | None = Field(description="Days between irrigations at the mean daily deficit.")
    net_per_irrigation_mm: float | None
    gross_per_irrigation_mm: float | None
    gross_per_irrigation_m3: float | None
    beyond_forecast: bool = Field(
        default=False,
        description="True when the interval is longer than the forecast period, i.e. "
                    "extrapolated from the mean daily deficit.",
    )
    advice: str


class Source(BaseModel):
    title: str
    organization: str
    reference: str


class CalculationResult(BaseModel):
    crop_type: CropType
    growth_stage: str
    irrigation_method: IrrigationMethod
    area_ha: float
    crop_coefficient: CropCoefficient
    daily: list[DailyRequirement]
    totals: Totals
    soil_water: SoilWaterStatus
    schedule: Schedule
    assumptions: list[str]
    limitations: list[str]
    sources: list[Source]


class SkippedCrop(BaseModel):
    crop_id: int
    crop_type: str
    reason: str


class CropRecommendation(BaseModel):
    crop_id: int
    variety: str | None
    result: CalculationResult


class FieldRecommendation(BaseModel):
    farm_id: int
    field_id: int
    field_name: str
    forecast_days: int
    weather_source: str
    recommendations: list[CropRecommendation]
    skipped: list[SkippedCrop]
    disclaimer: str = (
        "Estimates based on FAO-56 and weather forecasts. Adjust to field observations "
        "(soil moisture, plant condition) and local technical advice."
    )
