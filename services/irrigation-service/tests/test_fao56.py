"""FAO-56 formula tests, checked against hand calculations and FAO-56 examples."""

import pytest

from app.services import fao56


def test_wind_speed_at_2m_matches_fao56_example_14():
    # FAO-56 Example 14: 3.2 m/s measured at 10 m -> u2 = 2.4 m/s (factor 0.748).
    assert fao56.wind_speed_at_2m(3.2, 10) == pytest.approx(2.39, abs=0.01)
    assert fao56.wind_speed_at_2m(2.0, 2) == pytest.approx(2.0, abs=0.01)


def test_wind_height_must_be_valid():
    with pytest.raises(ValueError):
        fao56.wind_speed_at_2m(3, 0.05)


def test_climate_adjustment_eq_62():
    # 1.15 + [0.04 (3 - 2) - 0.004 (30 - 45)] (0.6 / 3)^0.3 = 1.15 + 0.10 x 0.6170
    assert fao56.adjust_kc_for_climate(1.15, u2=3, rh_min=30, height_m=0.6) == pytest.approx(1.2117, abs=1e-4)
    # Standard climate (u2 = 2, RHmin = 45): no change.
    assert fao56.adjust_kc_for_climate(1.15, u2=2, rh_min=45, height_m=0.6) == pytest.approx(1.15)


def test_climate_adjustment_not_applied_for_low_kc_and_inputs_clamped():
    assert fao56.adjust_kc_for_climate(0.40, u2=6, rh_min=20, height_m=1) == 0.40
    clamped = fao56.adjust_kc_for_climate(1.15, u2=12, rh_min=5, height_m=0.6)
    assert clamped == pytest.approx(fao56.adjust_kc_for_climate(1.15, u2=6, rh_min=20, height_m=0.6))


@pytest.mark.parametrize(
    "crop,stage,progress,expected",
    [
        ("tomato", "initial", 0.5, 0.60),
        ("tomato", "development", 0.0, 0.60),
        ("tomato", "development", 0.5, 0.875),  # 0.60 + 0.5 x (1.15 - 0.60)
        ("tomato", "development", 1.0, 1.15),
        ("tomato", "mid_season", 0.5, 1.15),
        ("tomato", "late_season", 0.5, 0.975),  # 1.15 + 0.5 x (0.80 - 1.15)
        ("olive", "dormancy", 0.5, 0.65),
        ("olive", "pit_hardening", 0.5, 0.70),
        ("olive", "fruit_maturation", 1.0, 0.70),
    ],
)
def test_crop_coefficient_by_stage(crop, stage, progress, expected):
    assert fao56.crop_coefficient(crop, stage, progress).kc == pytest.approx(expected)


def test_crop_coefficient_with_climate_adjusts_mid_and_end_only():
    kc = fao56.crop_coefficient("tomato", "mid_season", u2=3, rh_min=30)
    assert kc.climate_adjusted and kc.kc == pytest.approx(1.212, abs=1e-3)
    assert kc.kc_ini == 0.60
    assert fao56.crop_coefficient("tomato", "initial", u2=3, rh_min=30).kc == 0.60


@pytest.mark.parametrize(
    "args",
    [("wheat", "initial"), ("tomato", "flowering"), ("olive", "mid_season")],
)
def test_crop_coefficient_rejects_unknown_inputs(args):
    with pytest.raises(ValueError):
        fao56.crop_coefficient(*args)


def test_crop_coefficient_rejects_bad_progress():
    with pytest.raises(ValueError):
        fao56.crop_coefficient("tomato", "development", 1.5)


def test_etc_and_effective_rain():
    assert fao56.crop_evapotranspiration(1.15, 5.0) == pytest.approx(5.75)
    assert fao56.effective_rainfall(10, 0.8) == pytest.approx(8.0)
    assert fao56.effective_rainfall(-1, 0.8) == 0.0


@pytest.mark.parametrize("etc,expected", [(5.0, 0.40), (3.0, 0.48), (20.0, 0.10), (-20.0, 0.80)])
def test_depletion_fraction_adjustment(etc, expected):
    assert fao56.adjusted_depletion_fraction(0.40, etc) == pytest.approx(expected)


def test_taw_and_raw_eq_82_83():
    taw = fao56.total_available_water(fao56.SOILS["sandy_loam"], 1.1)
    assert taw == pytest.approx(132.0)  # 1000 x (0.23 - 0.11) x 1.1
    assert fao56.readily_available_water(taw, 0.40) == pytest.approx(52.8)


def test_soil_table_midpoints_fall_inside_fao56_taw_ranges():
    # FAO-56 Table 19 lists (theta FC - theta WP) ranges per texture.
    ranges = {"sand": (0.05, 0.11), "loamy_sand": (0.06, 0.12), "sandy_loam": (0.11, 0.15),
              "loam": (0.13, 0.18), "silt_loam": (0.13, 0.19), "clay": (0.12, 0.20)}
    for texture, (low, high) in ranges.items():
        soil = fao56.SOILS[texture]
        assert low <= soil.field_capacity - soil.wilting_point <= high, texture


def test_volume_conversions():
    assert fao56.depth_to_volume_m3(10, 2) == 200  # 10 mm on 2 ha
    assert fao56.litres_per_plant(5, 1, 400) == 125  # 5 mm on 1 ha, 400 trees
