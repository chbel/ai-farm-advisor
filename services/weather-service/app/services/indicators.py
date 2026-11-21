"""Agricultural weather indicators and rule-based risk alerts.

Every alert carries the exact rule that fired and the basis of that rule.
Thresholds are deliberately conservative screening values, not diagnoses:
the AI Advisor and the farmer decide what to do with them.

Sources:
- Reference evapotranspiration (ET0) is computed by Open-Meteo with the
  FAO-56 Penman-Monteith equation (Allen et al., 1998, FAO Irrigation and
  Drainage Paper 56). We do not recompute it here.
- Growing degree days: standard averaging method,
  GDD = max(0, (Tmax + Tmin) / 2 - Tbase). Tbase is crop-specific and is an
  input (default 10 °C, a base commonly used for tomato).
- Late blight: the Smith Period (Smith, 1956), the classic UK forecasting
  criterion for Phytophthora infestans: two consecutive days, each with a
  minimum temperature >= 10 °C and at least 11 hours with relative humidity
  >= 90 %.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.clients.open_meteo import HourlySeries
from app.schemas.weather import (
    DailyIndicators,
    DailyWeather,
    DailyWeatherWithIndicators,
    PeriodSummary,
    RiskAlert,
    RiskLevel,
    RiskType,
)

DEFAULT_BASE_TEMPERATURE_C = 10.0
ALL_CROPS = ["olive", "tomato"]


@dataclass(frozen=True)
class Thresholds:
    frost_c: float = 0.0  # Tmin at or below: frost
    near_frost_c: float = 2.0  # Tmin at or below: possible frost at plant level
    heat_c: float = 35.0
    extreme_heat_c: float = 40.0
    heavy_rain_mm: float = 20.0
    very_heavy_rain_mm: float = 50.0
    strong_wind_kmh: float = 40.0
    very_strong_wind_kmh: float = 60.0
    smith_min_temp_c: float = 10.0
    smith_humid_hours: int = 11
    smith_rh_pct: float = 90.0


THRESHOLDS = Thresholds()

_CONFIGURABLE = "Default screening threshold (configurable), not a crop-specific standard."


def growing_degree_days(tmax: float | None, tmin: float | None, base: float) -> float | None:
    if tmax is None or tmin is None:
        return None
    return round(max(0.0, (tmax + tmin) / 2 - base), 2)


def water_balance(precipitation: float | None, et0: float | None) -> float | None:
    if precipitation is None or et0 is None:
        return None
    return round(precipitation - et0, 2)


def with_indicators(days: list[DailyWeather], base: float) -> list[DailyWeatherWithIndicators]:
    return [
        DailyWeatherWithIndicators(
            **day.model_dump(),
            indicators=DailyIndicators(
                growing_degree_days=growing_degree_days(
                    day.temperature_max_c, day.temperature_min_c, base
                ),
                water_balance_mm=water_balance(day.precipitation_mm, day.et0_mm),
            ),
        )
        for day in days
    ]


def summarize(days: list[DailyWeatherWithIndicators], base: float) -> PeriodSummary:
    """Totals over the period. Days with missing values are excluded and counted."""

    complete = [
        d for d in days
        if None not in (d.precipitation_mm, d.et0_mm, d.temperature_max_c, d.temperature_min_c)
    ]

    def total(values: list[float]) -> float | None:
        return round(sum(values), 2) if complete else None

    return PeriodSummary(
        days=len(days),
        total_precipitation_mm=total([d.precipitation_mm for d in complete]),
        total_et0_mm=total([d.et0_mm for d in complete]),
        water_balance_mm=total([d.indicators.water_balance_mm for d in complete]),
        growing_degree_days=total([d.indicators.growing_degree_days for d in complete]),
        base_temperature_c=base,
        min_temperature_c=min((d.temperature_min_c for d in complete), default=None),
        max_temperature_c=max((d.temperature_max_c for d in complete), default=None),
        missing_days=len(days) - len(complete),
    )


# ------------------------------------------------------------------ alerts


def _daily_alerts(day: DailyWeather, t: Thresholds) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    tmin, tmax = day.temperature_min_c, day.temperature_max_c
    rain, wind = day.precipitation_mm, day.wind_speed_max_kmh

    if tmin is not None and tmin <= t.near_frost_c:
        frost = tmin <= t.frost_c
        alerts.append(RiskAlert(
            type=RiskType.FROST,
            level=RiskLevel.HIGH if frost else RiskLevel.MODERATE,
            date=day.date,
            crops=ALL_CROPS,
            message=(f"Minimum temperature {tmin:g} °C: frost expected." if frost else
                     f"Minimum temperature {tmin:g} °C: frost possible on plants on clear, calm nights."),
            rule=f"Tmin <= {t.frost_c:g} °C (high) or <= {t.near_frost_c:g} °C (moderate)",
            basis=("Forecast air temperature is at 2 m; leaves and fruit can be colder on clear, "
                   "calm nights (radiation frost). Tomato is frost-sensitive; olive damage depends "
                   "on variety and frost severity."),
        ))

    if tmax is not None and tmax >= t.heat_c:
        alerts.append(RiskAlert(
            type=RiskType.HEAT_STRESS,
            level=RiskLevel.HIGH if tmax >= t.extreme_heat_c else RiskLevel.MODERATE,
            date=day.date,
            crops=ALL_CROPS,
            message=f"Maximum temperature {tmax:g} °C: heat stress and high water demand.",
            rule=f"Tmax >= {t.heat_c:g} °C (moderate) or >= {t.extreme_heat_c:g} °C (high)",
            basis=f"{_CONFIGURABLE} High temperatures are known to impair tomato fruit set.",
        ))

    if rain is not None and rain >= t.heavy_rain_mm:
        alerts.append(RiskAlert(
            type=RiskType.HEAVY_RAIN,
            level=RiskLevel.HIGH if rain >= t.very_heavy_rain_mm else RiskLevel.MODERATE,
            date=day.date,
            crops=ALL_CROPS,
            message=f"{rain:g} mm of rain forecast: possible waterlogging, erosion or runoff; "
                    "postpone fertilizer and treatment applications.",
            rule=f"Daily precipitation >= {t.heavy_rain_mm:g} mm (moderate) "
                 f"or >= {t.very_heavy_rain_mm:g} mm (high)",
            basis=_CONFIGURABLE,
        ))

    if wind is not None and wind >= t.strong_wind_kmh:
        alerts.append(RiskAlert(
            type=RiskType.STRONG_WIND,
            level=RiskLevel.HIGH if wind >= t.very_strong_wind_kmh else RiskLevel.MODERATE,
            date=day.date,
            crops=ALL_CROPS,
            message=f"Wind up to {wind:g} km/h: avoid spraying (drift) and check supports.",
            rule=f"Max wind at 10 m >= {t.strong_wind_kmh:g} km/h (moderate) "
                 f"or >= {t.very_strong_wind_kmh:g} km/h (high)",
            basis=_CONFIGURABLE,
        ))
    return alerts


def smith_periods(hourly: HourlySeries, t: Thresholds = THRESHOLDS) -> list[date]:
    """Dates on which a Smith Period is completed (second qualifying day)."""

    by_day: dict[date, list[tuple[float, float]]] = defaultdict(list)
    for moment, temp, rh in zip(hourly.time, hourly.temperature_c, hourly.relative_humidity_pct):
        if temp is not None and rh is not None:
            by_day[moment.date()].append((temp, rh))

    def qualifies(day: date) -> bool:
        hours = by_day.get(day, [])
        if len(hours) < 24:  # incomplete day: do not guess
            return False
        humid_hours = sum(1 for _, rh in hours if rh >= t.smith_rh_pct)
        return min(temp for temp, _ in hours) >= t.smith_min_temp_c and humid_hours >= t.smith_humid_hours

    days = sorted(by_day)
    return [
        current for previous, current in zip(days, days[1:])
        if (current - previous).days == 1 and qualifies(previous) and qualifies(current)
    ]


def risk_alerts(
    days: list[DailyWeather], hourly: HourlySeries | None, t: Thresholds = THRESHOLDS
) -> list[RiskAlert]:
    alerts = [alert for day in days for alert in _daily_alerts(day, t)]

    if hourly is not None:
        for day in smith_periods(hourly, t):
            alerts.append(RiskAlert(
                type=RiskType.LATE_BLIGHT_CONDITIONS,
                level=RiskLevel.HIGH,
                date=day,
                crops=["tomato"],
                message="Weather favourable to tomato late blight (Phytophthora infestans). "
                        "Inspect plants; consider preventive protection per local advice.",
                rule=(f"Smith Period: two consecutive days with Tmin >= {t.smith_min_temp_c:g} °C "
                      f"and >= {t.smith_humid_hours} h of RH >= {t.smith_rh_pct:g} %"),
                basis="Smith (1956) late blight criterion, derived for UK potato crops; indicates "
                      "favourable conditions only, not presence of the disease.",
            ))

    return sorted(alerts, key=lambda a: (a.date, a.type.value))
