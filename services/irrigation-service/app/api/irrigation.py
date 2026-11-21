from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.clients.upstream import farm_url, get_json, weather_url
from app.core.security import CurrentPrincipal
from app.schemas.irrigation import (
    CalculationRequest,
    CalculationResult,
    CropRecommendation,
    DailyWeatherInput,
    FieldRecommendation,
    SkippedCrop,
)
from app.services import fao56, planner

router = APIRouter(prefix="/irrigation", tags=["Irrigation"])

_RESPONSES = {401: {"description": "Missing or invalid token."}}


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


Http = Annotated[httpx.AsyncClient, Depends(get_http)]


@router.get("/reference", summary="FAO-56 parameters used by the calculations")
async def reference(_: CurrentPrincipal) -> dict:
    """Crop coefficients, root depths, soil water and efficiencies, with sources."""
    return {
        "crops": {name: vars(params) for name, params in fao56.CROPS.items()},
        "stage_positions": {
            crop: {stage: pos.value for stage, pos in stages.items()}
            for crop, stages in fao56.STAGE_POSITIONS.items()
        },
        "soils": {name: vars(soil) for name, soil in fao56.SOILS.items()},
        "application_efficiency": fao56.APPLICATION_EFFICIENCY,
        "sources": [s.model_dump() for s in planner.SOURCES],
    }


@router.post("/calculate", response_model=CalculationResult, responses=_RESPONSES)
async def calculate(data: CalculationRequest, _: CurrentPrincipal) -> CalculationResult:
    """Stateless FAO-56 calculation from explicit crop, field and weather inputs."""
    return planner.calculate(data)


@router.get(
    "/farms/{farm_id}/fields/{field_id}",
    response_model=FieldRecommendation,
    responses={**_RESPONSES, 404: {"description": "Farm or field not found."}},
)
async def field_recommendation(
    farm_id: int,
    field_id: int,
    request: Request,
    _: CurrentPrincipal,
    http: Http,
    days: Annotated[int, Query(ge=1, le=16)] = 7,
    stage_progress: Annotated[
        float, Query(ge=0, le=1, description="Position inside development/late stages.")
    ] = 0.5,
) -> FieldRecommendation:
    """Irrigation recommendation for every active crop on a field, using the
    recorded growth stage and the weather forecast at the farm."""

    auth = request.headers["authorization"]
    base = f"/farms/{farm_id}/fields/{field_id}"
    field = await get_json(http, "farm-service", farm_url(base), auth)
    crops = await get_json(http, "farm-service", farm_url(f"{base}/crops"), auth)
    forecast = await get_json(
        http, "weather-service", weather_url(f"/weather/farms/{farm_id}?days={days}"), auth
    )

    weather = [
        DailyWeatherInput(date=d["date"], et0_mm=d["et0_mm"], precipitation_mm=d["precipitation_mm"] or 0.0)
        for d in forecast["daily"]
        if d.get("et0_mm") is not None
    ]
    if not weather:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No usable ET0 data in the forecast.")

    active = [c for c in crops if c["status"] == "active"]
    recommendations: list[CropRecommendation] = []
    skipped: list[SkippedCrop] = []
    for crop in crops:
        if crop["status"] != "active":
            skipped.append(SkippedCrop(crop_id=crop["id"], crop_type=crop["crop_type"],
                                       reason=f"Crop status is '{crop['status']}'."))
        elif crop["growth_stage"] is None:
            skipped.append(SkippedCrop(crop_id=crop["id"], crop_type=crop["crop_type"],
                                       reason="No growth stage recorded; record one in the farm journal."))
        else:
            result = planner.calculate(CalculationRequest(
                crop_type=crop["crop_type"],
                growth_stage=crop["growth_stage"],
                stage_progress=stage_progress,
                area_ha=field["area_ha"],
                irrigation_method=field["irrigation_method"],
                soil_texture=field["soil_texture"],
                plant_count=crop["plant_count"],
                weather=weather,
            ))
            if len(active) > 1:
                result.limitations.append(
                    "Several active crops share this field; each is computed on the full field "
                    "area. Split the field to get accurate volumes."
                )
            if len(weather) < len(forecast["daily"]):
                result.limitations.append("Some forecast days had no ET0 and were left out.")
            recommendations.append(CropRecommendation(
                crop_id=crop["id"], variety=crop["variety"], result=result,
            ))

    return FieldRecommendation(
        farm_id=farm_id,
        field_id=field_id,
        field_name=field["name"],
        forecast_days=len(weather),
        weather_source=f"{forecast['source']['attribution']} ({forecast['source']['dataset']})",
        recommendations=recommendations,
        skipped=skipped,
    )
