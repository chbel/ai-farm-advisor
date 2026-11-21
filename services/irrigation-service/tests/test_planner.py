"""Recommendation logic (pure, no HTTP)."""

from datetime import date, timedelta

import pytest

from app.schemas.irrigation import CalculationRequest
from app.services.planner import calculate


def request(**overrides) -> CalculationRequest:
    data = dict(
        crop_type="tomato", growth_stage="mid_season", area_ha=2.0,
        irrigation_method="sprinkler", soil_texture="sandy_loam",
        weather=[{"date": (date(2026, 7, 1) + timedelta(days=i)).isoformat(),
                  "et0_mm": 5.0, "precipitation_mm": 0.0} for i in range(7)],
    )
    data.update(overrides)
    return CalculationRequest(**data)


def test_tomato_mid_season_sprinkler():
    result = calculate(request())

    assert result.crop_coefficient.value == 1.15
    assert result.daily[0].etc_mm == 5.75
    t = result.totals
    assert (t.etc_mm, t.net_irrigation_mm) == (40.25, 40.25)
    assert t.application_efficiency == 0.75
    assert t.gross_irrigation_mm == pytest.approx(53.67)  # 40.25 / 0.75
    assert t.gross_volume_m3 == pytest.approx(1073.3)  # 53.67 mm x 2 ha x 10

    soil = result.soil_water
    assert soil.available
    assert soil.depletion_fraction_p == pytest.approx(0.37)  # 0.40 + 0.04 (5 - 5.75)
    assert soil.total_available_water_mm == 132.0
    assert soil.readily_available_water_mm == pytest.approx(48.8)

    assert result.schedule.interval_days == 8  # floor(48.84 / 5.75)
    assert result.schedule.net_per_irrigation_mm == 46.0
    assert result.schedule.beyond_forecast  # 8-day interval, 7-day forecast
    assert "No irrigation expected within the 7-day forecast" in result.schedule.advice
    assert any("Eq. 56" in s.reference for s in result.sources)


def test_short_interval_inside_forecast():
    weather = [{"date": f"2026-07-{d:02d}", "et0_mm": 8.0} for d in range(1, 15)]
    result = calculate(request(weather=weather, soil_texture="sand"))

    # ETc 9.2; p = 0.40 + 0.04 (5 - 9.2) = 0.232; TAW = 1000 x 0.075 x 1.1 = 82.5;
    # RAW = 19.1 mm -> every 2 days.
    assert result.schedule.interval_days == 2
    assert not result.schedule.beyond_forecast
    assert result.schedule.advice.startswith("Irrigate about every 2 day(s)")


def test_rain_offsets_demand_with_effective_fraction():
    weather = [{"date": "2026-03-01", "et0_mm": 3.0, "precipitation_mm": 10.0},
               {"date": "2026-03-02", "et0_mm": 3.0, "precipitation_mm": 0.0}]
    result = calculate(request(weather=weather, growth_stage="initial"))

    # ETc = 2 x 0.6 x 3 = 3.6 mm; effective rain = 0.8 x 10 = 8 mm -> no irrigation.
    assert result.totals.effective_rain_mm == 8.0
    assert result.totals.net_irrigation_mm == 0.0
    assert result.daily[0].deficit_mm == pytest.approx(-6.2)
    assert result.schedule.interval_days is None
    assert "no irrigation needed" in result.schedule.advice


def test_drip_gives_daily_amounts_and_litres_per_plant():
    result = calculate(request(crop_type="olive", growth_stage="pit_hardening",
                               irrigation_method="drip", plant_count=400, area_ha=1.0))

    # ETc = 0.70 x 5 = 3.5 mm/day; gross = 3.5 / 0.9 = 3.89 mm/day.
    assert result.schedule.interval_days == 1
    assert result.schedule.gross_per_irrigation_mm == pytest.approx(3.89)
    assert result.schedule.gross_per_irrigation_m3 == pytest.approx(38.9)
    # 7-day gross 27.22 mm on 1 ha / 400 trees = 680.6 L per tree.
    assert result.totals.litres_per_plant == pytest.approx(680.6, abs=0.1)
    assert any("40-60 % ground cover" in item for item in result.limitations)
    assert any("mapped onto the FAO-56 Kc curve" in item for item in result.assumptions)


def test_rainfed_reports_deficit_without_volumes():
    result = calculate(request(irrigation_method="rainfed"))

    assert result.totals.net_irrigation_mm == 40.25
    assert result.totals.gross_irrigation_mm is None
    assert result.totals.gross_volume_m3 is None
    assert result.schedule.interval_days is None
    assert any("rainfed" in item.lower() for item in result.limitations)


@pytest.mark.parametrize("texture", ["clay_loam", "unknown"])
def test_unsupported_soil_texture_is_not_guessed(texture):
    result = calculate(request(soil_texture=texture))

    assert not result.soil_water.available
    assert "Table 19" in result.soil_water.reason
    assert result.schedule.interval_days is None
    assert result.totals.gross_irrigation_mm == pytest.approx(53.67)  # volumes still computed


def test_climate_input_raises_kc_and_is_reported():
    plain = calculate(request())
    adjusted = calculate(request(climate={"wind_speed_2m_ms": 3, "rh_min_pct": 30}))

    assert adjusted.crop_coefficient.climate_adjusted
    assert adjusted.crop_coefficient.value > plain.crop_coefficient.value
    assert any("without climate adjustment" in a for a in plain.assumptions)
    assert not any("without climate adjustment" in a for a in adjusted.assumptions)


def test_root_depth_override():
    result = calculate(request(root_depth_m=0.5))
    assert result.soil_water.total_available_water_mm == 60.0  # 1000 x 0.12 x 0.5


@pytest.mark.parametrize(
    "overrides",
    [
        {"growth_stage": "flowering"},  # olive stage on tomato
        {"weather": []},
        {"weather": [{"date": "2026-07-01", "et0_mm": 5}, {"date": "2026-07-01", "et0_mm": 4}]},
        {"weather": [{"date": "2026-07-01", "et0_mm": -1}]},
        {"stage_progress": 1.2},
        {"area_ha": 0},
    ],
)
def test_request_validation(overrides):
    with pytest.raises(ValueError):
        request(**overrides)
