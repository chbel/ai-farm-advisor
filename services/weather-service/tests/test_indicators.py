"""Unit tests for agricultural indicators and alert rules (no HTTP)."""

from datetime import date, datetime, timedelta

import pytest

from app.clients.open_meteo import HourlySeries
from app.schemas.weather import DailyWeather, RiskType
from app.services import indicators as ind


def day(d: int = 0, **values) -> DailyWeather:
    base = dict(
        temperature_max_c=25.0, temperature_min_c=15.0, precipitation_mm=0.0,
        et0_mm=4.0, wind_speed_max_kmh=10.0, relative_humidity_mean_pct=60.0,
        shortwave_radiation_mj_m2=15.0, weather_code=1, weather_description="Mainly clear",
    )
    base.update(values)
    return DailyWeather(date=date(2026, 6, 1) + timedelta(days=d), **base)


@pytest.mark.parametrize(
    "tmax,tmin,base,expected",
    [(30, 20, 10, 15.0), (12, 4, 10, 0.0), (20, 10, 10, 5.0), (None, 10, 10, None)],
)
def test_growing_degree_days(tmax, tmin, base, expected):
    assert ind.growing_degree_days(tmax, tmin, base) == expected


def test_water_balance():
    assert ind.water_balance(1.0, 4.66) == -3.66
    assert ind.water_balance(None, 4.0) is None


def test_summary_excludes_and_counts_incomplete_days():
    days = ind.with_indicators(
        [day(0, precipitation_mm=2.0), day(1, et0_mm=None), day(2, precipitation_mm=0.0)], 10
    )
    summary = ind.summarize(days, 10)

    assert summary.days == 3
    assert summary.missing_days == 1
    assert summary.total_precipitation_mm == 2.0
    assert summary.total_et0_mm == 8.0
    assert summary.water_balance_mm == -6.0
    assert summary.growing_degree_days == 20.0  # 2 days x ((25+15)/2 - 10)


def test_summary_of_only_missing_days_is_null():
    summary = ind.summarize(ind.with_indicators([day(0, et0_mm=None)], 10), 10)
    assert summary.total_et0_mm is None and summary.missing_days == 1


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"temperature_min_c": -1.0}, (RiskType.FROST, "high")),
        ({"temperature_min_c": 1.5}, (RiskType.FROST, "moderate")),
        ({"temperature_max_c": 36.0}, (RiskType.HEAT_STRESS, "moderate")),
        ({"temperature_max_c": 41.0}, (RiskType.HEAT_STRESS, "high")),
        ({"precipitation_mm": 25.0}, (RiskType.HEAVY_RAIN, "moderate")),
        ({"precipitation_mm": 60.0}, (RiskType.HEAVY_RAIN, "high")),
        ({"wind_speed_max_kmh": 45.0}, (RiskType.STRONG_WIND, "moderate")),
        ({"wind_speed_max_kmh": 70.0}, (RiskType.STRONG_WIND, "high")),
    ],
)
def test_daily_alert_rules(values, expected):
    alerts = ind.risk_alerts([day(0, **values)], hourly=None)

    assert [(a.type, a.level.value) for a in alerts] == [expected]
    assert alerts[0].rule and alerts[0].basis


def test_no_alerts_on_mild_day_and_boundaries():
    assert ind.risk_alerts([day(0)], hourly=None) == []
    # Just outside each threshold.
    assert ind.risk_alerts([day(0, temperature_min_c=2.1, temperature_max_c=34.9,
                                precipitation_mm=19.9, wind_speed_max_kmh=39.9)], None) == []


def hourly(days: int, temps, rhs) -> HourlySeries:
    start = datetime(2026, 11, 1)
    times = [start + timedelta(hours=h) for h in range(24 * days)]
    return HourlySeries(time=times, temperature_c=temps, relative_humidity_pct=rhs)


def humid_day(humid_hours: int, tmin: float = 12.0) -> tuple[list, list]:
    return [tmin] * 24, [95.0] * humid_hours + [70.0] * (24 - humid_hours)


def test_smith_period_needs_two_consecutive_qualifying_days():
    t1, r1 = humid_day(11)
    t2, r2 = humid_day(12)
    t3, r3 = humid_day(5)  # not humid enough
    series = hourly(3, t1 + t2 + t3, r1 + r2 + r3)

    assert ind.smith_periods(series) == [date(2026, 11, 2)]


@pytest.mark.parametrize(
    "first,second",
    [
        (humid_day(11, tmin=9.9), humid_day(11)),  # too cold on day 1
        (humid_day(10), humid_day(11)),  # 10 h humid only on day 1
    ],
)
def test_smith_period_not_met(first, second):
    series = hourly(2, first[0] + second[0], first[1] + second[1])
    assert ind.smith_periods(series) == []


def test_smith_period_ignores_incomplete_days():
    t, r = humid_day(11)
    series = hourly(2, t + t[:20], r + r[:20])  # second day has only 20 hours
    series.time = series.time[:44]
    assert ind.smith_periods(series) == []


def test_late_blight_alert_is_tomato_only():
    t, r = humid_day(12)
    alerts = ind.risk_alerts([], hourly(2, t + t, r + r))

    assert [(a.type, a.crops) for a in alerts] == [(RiskType.LATE_BLIGHT_CONDITIONS, ["tomato"])]
    assert "Smith" in alerts[0].basis
