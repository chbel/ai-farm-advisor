from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentPrincipal
from app.models import Farm
from app.models.enums import (
    STAGES_BY_CROP,
    ActivityType,
    CropStatus,
    Governorate,
    IrrigationMethod,
    SoilTexture,
)
from app.schemas.farm import FarmCreate, FarmList, FarmResponse, FarmUpdate
from app.services.access import allocated_area, get_farm, unprocessable

router = APIRouter(prefix="/farms", tags=["Farms"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
_RESPONSES = {401: {"description": "Missing or invalid token."}}
_ITEM_RESPONSES = {**_RESPONSES, 404: {"description": "Farm not found."}}


@router.get("/catalog", summary="Allowed values for farm, field and crop attributes")
async def read_catalog(_: CurrentPrincipal) -> dict:
    """Reference lists for forms (dropdowns) and for other services."""
    return {
        "governorates": [g.value for g in Governorate],
        "soil_textures": [s.value for s in SoilTexture],
        "irrigation_methods": [m.value for m in IrrigationMethod],
        "crop_statuses": [s.value for s in CropStatus],
        "activity_types": [a.value for a in ActivityType],
        "crops": {
            crop.value: {"growth_stages": [s.value for s in stages]}
            for crop, stages in STAGES_BY_CROP.items()
        },
    }


@router.post("", response_model=FarmResponse, status_code=status.HTTP_201_CREATED, responses=_RESPONSES)
async def create_farm(data: FarmCreate, principal: CurrentPrincipal, db: DbSession) -> Farm:
    """Create a farm owned by the authenticated user."""
    farm = Farm(owner_id=principal.user_id, **data.model_dump(mode="json"))
    db.add(farm)
    await db.commit()
    await db.refresh(farm)
    return farm


@router.get("", response_model=FarmList, responses=_RESPONSES)
async def list_farms(
    principal: CurrentPrincipal,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    owner_id: Annotated[int | None, Query(description="Admins only: filter by owner.")] = None,
) -> FarmList:
    """List the caller's farms (administrators see all, optionally by owner)."""

    query = select(Farm)
    if not principal.is_admin:
        query = query.where(Farm.owner_id == principal.user_id)
    elif owner_id is not None:
        query = query.where(Farm.owner_id == owner_id)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    farms = await db.scalars(query.order_by(Farm.id.desc()).limit(limit).offset(offset))
    return FarmList(
        items=[FarmResponse.model_validate(f) for f in farms],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get("/{farm_id}", response_model=FarmResponse, responses=_ITEM_RESPONSES)
async def read_farm(farm_id: int, principal: CurrentPrincipal, db: DbSession) -> Farm:
    return await get_farm(db, principal, farm_id)


@router.patch("/{farm_id}", response_model=FarmResponse, responses=_ITEM_RESPONSES)
async def update_farm(
    farm_id: int, data: FarmUpdate, principal: CurrentPrincipal, db: DbSession
) -> Farm:
    farm = await get_farm(db, principal, farm_id)
    changes = data.changes()

    new_total = changes.get("total_area_ha", farm.total_area_ha)
    if new_total is not None:
        used = await allocated_area(db, farm.id)
        if new_total + 1e-6 < used:
            raise unprocessable(
                f"Total area cannot be smaller than the area of its fields ({used:g} ha)."
            )

    for key, value in changes.items():
        setattr(farm, key, value)
    await db.commit()
    await db.refresh(farm)
    return farm


@router.delete("/{farm_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ITEM_RESPONSES)
async def delete_farm(farm_id: int, principal: CurrentPrincipal, db: DbSession) -> Response:
    """Delete a farm with all its fields, crops and history."""
    farm = await get_farm(db, principal, farm_id)
    await db.delete(farm)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
