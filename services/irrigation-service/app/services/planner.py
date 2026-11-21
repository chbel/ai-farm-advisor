"""Turns FAO-56 calculations into an irrigation recommendation.

Water balance simplification (stated in every result): the period is treated
as one balance, i.e. net irrigation = max(0, sum(ETc) - sum(effective rain)).
Rain surplus on one day offsets demand on later days (assumed stored in the
root zone), the root zone is assumed at field capacity at the start, and
deep percolation and runoff beyond the effective-rain fraction are ignored.
"""

from app.schemas.irrigation import (
    CalculationRequest,
    CalculationResult,
    CropCoefficient,
    DailyRequirement,
    IrrigationMethod,
    Schedule,
    SoilWaterStatus,
    Source,
    Totals,
)
from app.services import fao56

SOURCES = [
    Source(
        title="Crop evapotranspiration - Guidelines for computing crop water requirements",
        organization="FAO",
        reference="Irrigation and Drainage Paper 56 (Allen et al., 1998): Eq. 56, 62, 82, 83; "
                  "Tables 12, 19, 22",
    ),
    Source(
        title="Irrigation Water Management: Irrigation Scheduling",
        organization="FAO",
        reference="Training Manual No. 4 (Brouwer et al., 1989): field application efficiencies",
    ),
]

_BALANCE_ASSUMPTIONS = [
    "Single water balance over the period: rain surplus offsets later demand; "
    "root zone at field capacity at the start; no deep percolation.",
]


def _r(value: float, digits: int = 2) -> float:
    return round(value, digits)


def calculate(req: CalculationRequest) -> CalculationResult:
    crop = fao56.CROPS[req.crop_type.value]
    kc = fao56.crop_coefficient(
        req.crop_type.value,
        req.growth_stage,
        req.stage_progress,
        u2=req.climate.wind_speed_2m_ms if req.climate else None,
        rh_min=req.climate.rh_min_pct if req.climate else None,
    )

    assumptions = list(_BALANCE_ASSUMPTIONS)
    limitations: list[str] = []

    if kc.position in (fao56.StagePosition.DEVELOPMENT, fao56.StagePosition.LATE):
        assumptions.append(
            f"Kc interpolated at {req.stage_progress:.0%} of the {kc.position.value} stage."
        )
    if not kc.climate_adjusted:
        assumptions.append(
            "Kc taken from FAO-56 Table 12 without climate adjustment (Eq. 62). In dry, windy "
            "conditions (RHmin < 45 %, wind > 2 m/s) the true mid/late-season Kc is higher."
        )
    if req.crop_type.value == "olive":
        assumptions.append(
            "Olive stages were mapped onto the FAO-56 Kc curve by this service "
            "(dormancy = initial, vegetative growth = development, flowering to pit "
            "hardening = mid, fruit maturation = late)."
        )
        limitations.append(
            "Olive Kc assumes 40-60 % ground cover by canopy. Widely spaced or young "
            "orchards use less water; follow local guidance for low-density plantations."
        )
    assumptions.append(f"Crop parameters: {crop.notes}")
    assumptions.append(
        f"Effective rain = {req.effective_rain_fraction:.0%} of precipitation (fixed-percentage method)."
    )

    # Daily balance.
    daily = []
    for day in req.weather:
        etc = fao56.crop_evapotranspiration(kc.kc, day.et0_mm)
        peff = fao56.effective_rainfall(day.precipitation_mm, req.effective_rain_fraction)
        daily.append(DailyRequirement(
            date=day.date, et0_mm=_r(day.et0_mm), etc_mm=_r(etc),
            precipitation_mm=_r(day.precipitation_mm), effective_rain_mm=_r(peff),
            deficit_mm=_r(etc - peff),
        ))

    total_etc = sum(fao56.crop_evapotranspiration(kc.kc, d.et0_mm) for d in req.weather)
    total_peff = sum(
        fao56.effective_rainfall(d.precipitation_mm, req.effective_rain_fraction) for d in req.weather
    )
    net = max(0.0, total_etc - total_peff)

    # Gross requirement and volumes.
    efficiency = fao56.APPLICATION_EFFICIENCY.get(req.irrigation_method.value)
    rainfed = req.irrigation_method is IrrigationMethod.RAINFED
    gross = net / efficiency if efficiency else None
    if efficiency:
        assumptions.append(
            f"Application efficiency {efficiency:.0%} for {req.irrigation_method.value} "
            f"irrigation ({fao56.EFFICIENCY_SOURCE})."
        )
    if rainfed:
        limitations.append(
            "Field is rainfed: the net requirement is the crop water deficit; no irrigation "
            "volume is computed."
        )

    totals = Totals(
        days=len(daily),
        etc_mm=_r(total_etc),
        effective_rain_mm=_r(total_peff),
        net_irrigation_mm=_r(net),
        application_efficiency=efficiency,
        gross_irrigation_mm=_r(gross) if gross is not None else None,
        gross_volume_m3=_r(fao56.depth_to_volume_m3(gross, req.area_ha), 1) if gross is not None else None,
        litres_per_plant=(
            _r(fao56.litres_per_plant(gross, req.area_ha, req.plant_count), 1)
            if gross is not None and req.plant_count else None
        ),
    )

    soil = _soil_water(req, crop, total_etc / len(daily))
    schedule = _schedule(req, soil, net / len(daily), efficiency)
    if not soil.available:
        limitations.append(f"No irrigation interval: {soil.reason}")

    return CalculationResult(
        crop_type=req.crop_type,
        growth_stage=req.growth_stage,
        irrigation_method=req.irrigation_method,
        area_ha=req.area_ha,
        crop_coefficient=CropCoefficient(
            value=kc.kc, curve_position=kc.position.value, kc_ini=kc.kc_ini,
            kc_mid=kc.kc_mid, kc_end=kc.kc_end, climate_adjusted=kc.climate_adjusted,
        ),
        daily=daily,
        totals=totals,
        soil_water=soil,
        schedule=schedule,
        assumptions=assumptions,
        limitations=limitations,
        sources=SOURCES,
    )


def _soil_water(req: CalculationRequest, crop: fao56.CropParameters, mean_etc: float) -> SoilWaterStatus:
    root_depth = req.root_depth_m or crop.root_depth_m
    soil = fao56.SOILS.get(req.soil_texture)
    if soil is None:
        return SoilWaterStatus(
            available=False,
            root_depth_m=root_depth,
            reason=(f"soil texture '{req.soil_texture}' has no FAO-56 Table 19 values; "
                    f"set a texture among {', '.join(fao56.SOILS)} or measure soil water retention."),
        )
    p = fao56.adjusted_depletion_fraction(crop.depletion_fraction, mean_etc)
    taw = fao56.total_available_water(soil, root_depth)
    return SoilWaterStatus(
        available=True,
        root_depth_m=root_depth,
        depletion_fraction_p=_r(p, 3),
        total_available_water_mm=_r(taw, 1),
        readily_available_water_mm=_r(fao56.readily_available_water(taw, p), 1),
    )


def _schedule(
    req: CalculationRequest, soil: SoilWaterStatus, mean_net: float, efficiency: float | None
) -> Schedule:
    if req.irrigation_method is IrrigationMethod.RAINFED:
        return Schedule(interval_days=None, net_per_irrigation_mm=None, gross_per_irrigation_mm=None,
                        gross_per_irrigation_m3=None, advice="Rainfed field: no irrigation schedule.")
    if mean_net <= 0:
        return Schedule(interval_days=None, net_per_irrigation_mm=None, gross_per_irrigation_mm=None,
                        gross_per_irrigation_m3=None,
                        advice="Expected rain covers crop demand over the period: no irrigation needed.")
    if req.irrigation_method is IrrigationMethod.DRIP:
        gross_daily = mean_net / efficiency
        return Schedule(
            interval_days=1,
            net_per_irrigation_mm=_r(mean_net),
            gross_per_irrigation_mm=_r(gross_daily),
            gross_per_irrigation_m3=_r(fao56.depth_to_volume_m3(gross_daily, req.area_ha), 1),
            advice="Drip systems usually apply the daily requirement every day or every few days; "
                   "the amounts shown are per day.",
        )
    if not soil.available:
        return Schedule(interval_days=None, net_per_irrigation_mm=None, gross_per_irrigation_mm=None,
                        gross_per_irrigation_m3=None,
                        advice="Apply the gross requirement; the interval needs known soil texture.")

    raw = soil.readily_available_water_mm
    interval = max(1, int(raw // mean_net))
    net_each = interval * mean_net  # refill what was used, never more than RAW
    gross_each = net_each / efficiency
    period = len(req.weather)
    beyond = interval > period
    if beyond:
        advice = (f"No irrigation expected within the {period}-day forecast if the root zone "
                  f"starts near field capacity. At the current mean demand, readily available "
                  f"water ({raw:g} mm) lasts about {interval} days (extrapolated beyond the "
                  f"forecast); irrigate sooner if the soil is already dry.")
    else:
        advice = (f"Irrigate about every {interval} day(s), before readily available water "
                  f"({raw:g} mm) is used up.")
    return Schedule(
        interval_days=interval,
        net_per_irrigation_mm=_r(net_each),
        gross_per_irrigation_mm=_r(gross_each),
        gross_per_irrigation_m3=_r(fao56.depth_to_volume_m3(gross_each, req.area_ha), 1),
        beyond_forecast=beyond,
        advice=advice,
    )
