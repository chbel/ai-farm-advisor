"""FAO-56 crop water requirement calculations (pure functions, no I/O).

Reference: Allen, R.G., Pereira, L.S., Raes, D., Smith, M. (1998).
Crop evapotranspiration - Guidelines for computing crop water requirements.
FAO Irrigation and Drainage Paper 56. Rome: FAO.

Units: water depths in mm, ET rates in mm/day, root depth in m, soil water
content in m3/m3, wind in m/s, relative humidity in %, area in ha.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

FAO56 = "FAO Irrigation and Drainage Paper 56 (Allen et al., 1998)"


# --------------------------------------------------------------- crop data


@dataclass(frozen=True)
class CropParameters:
    """Single crop coefficients and root-zone parameters for one crop."""

    kc_ini: float  # FAO-56 Table 12
    kc_mid: float  # FAO-56 Table 12
    kc_end: float  # FAO-56 Table 12
    max_height_m: float  # FAO-56 Table 12, used by the climate adjustment
    root_depth_m: float  # FAO-56 Table 22 (midpoint of the range)
    depletion_fraction: float  # p, FAO-56 Table 22 (for ETc ~ 5 mm/day)
    notes: str


CROPS: dict[str, CropParameters] = {
    "tomato": CropParameters(
        kc_ini=0.60,
        kc_mid=1.15,
        kc_end=0.80,  # Table 12 gives 0.70-0.90; midpoint used
        max_height_m=0.6,
        root_depth_m=1.1,  # Table 22: 0.7-1.5 m
        depletion_fraction=0.40,
        notes="FAO-56 Table 12 Kc end range 0.70-0.90 (midpoint 0.80 used); "
              "Table 22 root depth 0.7-1.5 m (1.1 m used).",
    ),
    "olive": CropParameters(
        kc_ini=0.65,
        kc_mid=0.70,
        kc_end=0.70,
        max_height_m=4.0,  # Table 12: 3-5 m
        root_depth_m=1.45,  # Table 22: 1.2-1.7 m
        depletion_fraction=0.65,
        notes="FAO-56 Table 12 values for olives with 40-60 % ground cover by canopy; "
              "Table 22 root depth 1.2-1.7 m (1.45 m used).",
    ),
}


class StagePosition(StrEnum):
    """Where a growth stage sits on the FAO-56 Kc curve (Fig. 25)."""

    INITIAL = "initial"  # Kc = Kc ini
    DEVELOPMENT = "development"  # Kc rises linearly Kc ini -> Kc mid
    MID = "mid"  # Kc = Kc mid
    LATE = "late"  # Kc moves linearly Kc mid -> Kc end


# Tomato stages are the FAO-56 stages themselves. Olive phenological stages
# (Farm Service, simplified BBCH) are mapped onto the curve by this service;
# this mapping is our interpretation, not an FAO table.
STAGE_POSITIONS: dict[str, dict[str, StagePosition]] = {
    "tomato": {
        "initial": StagePosition.INITIAL,
        "development": StagePosition.DEVELOPMENT,
        "mid_season": StagePosition.MID,
        "late_season": StagePosition.LATE,
    },
    "olive": {
        "dormancy": StagePosition.INITIAL,
        "vegetative_growth": StagePosition.DEVELOPMENT,
        "flowering": StagePosition.MID,
        "fruit_set": StagePosition.MID,
        "pit_hardening": StagePosition.MID,
        "fruit_maturation": StagePosition.LATE,
    },
}


# ---------------------------------------------------------------- soil data


@dataclass(frozen=True)
class SoilWater:
    field_capacity: float  # theta FC, m3/m3
    wilting_point: float  # theta WP, m3/m3


# FAO-56 Table 19: midpoints of the typical ranges. Textures absent from the
# table (e.g. clay loam) are intentionally not guessed.
SOILS: dict[str, SoilWater] = {
    "sand": SoilWater(0.12, 0.045),  # FC 0.07-0.17, WP 0.02-0.07
    "loamy_sand": SoilWater(0.15, 0.065),  # FC 0.11-0.19, WP 0.03-0.10
    "sandy_loam": SoilWater(0.23, 0.11),  # FC 0.18-0.28, WP 0.06-0.16
    "loam": SoilWater(0.25, 0.12),  # FC 0.20-0.30, WP 0.07-0.17
    "silt_loam": SoilWater(0.29, 0.15),  # FC 0.22-0.36, WP 0.09-0.21
    "clay": SoilWater(0.36, 0.22),  # FC 0.32-0.40, WP 0.20-0.24
}


# Typical field application efficiencies. Source: Brouwer, C., Prins, K.,
# Heibloem, M. (1989). Irrigation Water Management: Irrigation Scheduling.
# FAO Training Manual No. 4.
APPLICATION_EFFICIENCY: dict[str, float] = {
    "surface": 0.60,
    "sprinkler": 0.75,
    "drip": 0.90,
}
EFFICIENCY_SOURCE = "FAO Irrigation Water Management Training Manual No. 4 (Brouwer et al., 1989)"


# ------------------------------------------------------------------ formulas


def wind_speed_at_2m(speed: float, height_m: float) -> float:
    """FAO-56 Eq. 47: u2 = uz * 4.87 / ln(67.8 z - 5.42)."""
    if height_m <= 0.1:
        raise ValueError("Measurement height must be above 0.1 m.")
    return speed * 4.87 / math.log(67.8 * height_m - 5.42)


def kmh_to_ms(speed_kmh: float) -> float:
    return speed_kmh / 3.6


def adjust_kc_for_climate(kc_table: float, u2: float, rh_min: float, height_m: float) -> float:
    """FAO-56 Eq. 62 (Kc mid) and Eq. 65 (Kc end).

    Kc = Kc(Tab) + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h / 3)^0.3
    Valid for 1 <= u2 <= 6 m/s and 20 <= RHmin <= 80 %; inputs are clamped to
    that range as recommended. Not applied when Kc(Tab) <= 0.45.
    """
    if kc_table <= 0.45:
        return kc_table
    u2 = min(max(u2, 1.0), 6.0)
    rh_min = min(max(rh_min, 20.0), 80.0)
    height_m = min(max(height_m, 0.1), 10.0)
    return kc_table + (0.04 * (u2 - 2) - 0.004 * (rh_min - 45)) * (height_m / 3) ** 0.3


@dataclass(frozen=True)
class KcResult:
    kc: float
    position: StagePosition
    kc_ini: float
    kc_mid: float
    kc_end: float
    climate_adjusted: bool


def crop_coefficient(
    crop_type: str,
    growth_stage: str,
    stage_progress: float = 0.5,
    *,
    u2: float | None = None,
    rh_min: float | None = None,
) -> KcResult:
    """Kc for a crop at a growth stage.

    `stage_progress` (0..1) positions the crop inside the development or late
    stage, where FAO-56 interpolates Kc linearly (Eq. 66). Kc mid and Kc end are
    climate-adjusted when both u2 and rh_min are given.
    """
    if crop_type not in CROPS:
        raise ValueError(f"Unsupported crop: {crop_type}")
    try:
        position = STAGE_POSITIONS[crop_type][growth_stage]
    except KeyError:
        raise ValueError(f"Unknown growth stage '{growth_stage}' for {crop_type}.") from None
    if not 0.0 <= stage_progress <= 1.0:
        raise ValueError("stage_progress must be between 0 and 1.")

    params = CROPS[crop_type]
    kc_mid, kc_end = params.kc_mid, params.kc_end
    adjusted = u2 is not None and rh_min is not None
    if adjusted:
        kc_mid = adjust_kc_for_climate(kc_mid, u2, rh_min, params.max_height_m)
        kc_end = adjust_kc_for_climate(kc_end, u2, rh_min, params.max_height_m)

    if position is StagePosition.INITIAL:
        kc = params.kc_ini
    elif position is StagePosition.DEVELOPMENT:
        kc = params.kc_ini + stage_progress * (kc_mid - params.kc_ini)
    elif position is StagePosition.MID:
        kc = kc_mid
    else:
        kc = kc_mid + stage_progress * (kc_end - kc_mid)

    return KcResult(
        kc=round(kc, 3), position=position, kc_ini=params.kc_ini,
        kc_mid=round(kc_mid, 3), kc_end=round(kc_end, 3), climate_adjusted=adjusted,
    )


def crop_evapotranspiration(kc: float, et0: float) -> float:
    """FAO-56 Eq. 56: ETc = Kc * ET0 (mm/day)."""
    return kc * et0


def effective_rainfall(precipitation_mm: float, fraction: float) -> float:
    """Fixed-percentage effective rainfall (one of the FAO CROPWAT methods)."""
    return max(precipitation_mm, 0.0) * fraction


def adjusted_depletion_fraction(p_table: float, etc_mm_day: float) -> float:
    """FAO-56 Table 22 footnote: p = p(Tab) + 0.04 (5 - ETc), limited to 0.1-0.8."""
    return min(max(p_table + 0.04 * (5 - etc_mm_day), 0.1), 0.8)


def total_available_water(soil: SoilWater, root_depth_m: float) -> float:
    """FAO-56 Eq. 82: TAW = 1000 (theta FC - theta WP) Zr (mm)."""
    return 1000 * (soil.field_capacity - soil.wilting_point) * root_depth_m


def readily_available_water(taw_mm: float, p: float) -> float:
    """FAO-56 Eq. 83: RAW = p TAW (mm)."""
    return p * taw_mm


def depth_to_volume_m3(depth_mm: float, area_ha: float) -> float:
    """1 mm of water over 1 ha = 10 m3."""
    return depth_mm * area_ha * 10


def litres_per_plant(depth_mm: float, area_ha: float, plants: int) -> float:
    """1 mm over 1 m2 = 1 L; 1 ha = 10 000 m2."""
    return depth_mm * area_ha * 10_000 / plants
