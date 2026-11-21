from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentPrincipal
from app.models import Crop, CropStageEvent
from app.schemas.farm import (
    CropCreate,
    CropResponse,
    CropUpdate,
    StageEventCreate,
    StageEventResponse,
    check_planting_date,
    check_stage,
)
from app.services.access import get_crop, get_farm, get_field, unprocessable

router = APIRouter(prefix="/farms/{farm_id}/fields/{field_id}/crops", tags=["Crops"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
_RESPONSES = {
    401: {"description": "Missing or invalid token."},
    404: {"description": "Farm, field or crop not found."},
}


async def _load_field(db: AsyncSession, principal, farm_id: int, field_id: int):
    farm = await get_farm(db, principal, farm_id)
    return await get_field(db, farm, field_id)


@router.post("", response_model=CropResponse, status_code=status.HTTP_201_CREATED, responses=_RESPONSES)
async def create_crop(
    farm_id: int, field_id: int, data: CropCreate, principal: CurrentPrincipal, db: DbSession
) -> Crop:
    field = await _load_field(db, principal, farm_id, field_id)

    crop = Crop(field_id=field.id, **data.model_dump(mode="json"))
    db.add(crop)
    await db.flush()
    # The current stage, if given, starts the stage history as observed today.
    if crop.growth_stage is not None:
        db.add(
            CropStageEvent(
                crop_id=crop.id,
                stage=crop.growth_stage,
                observed_on=date.today(),
                recorded_by=principal.user_id,
            )
        )
    await db.commit()
    await db.refresh(crop)
    return crop


@router.get("", response_model=list[CropResponse], responses=_RESPONSES)
async def list_crops(
    farm_id: int, field_id: int, principal: CurrentPrincipal, db: DbSession
) -> list[Crop]:
    field = await _load_field(db, principal, farm_id, field_id)
    return list(await db.scalars(select(Crop).where(Crop.field_id == field.id).order_by(Crop.id)))


@router.get("/{crop_id}", response_model=CropResponse, responses=_RESPONSES)
async def read_crop(
    farm_id: int, field_id: int, crop_id: int, principal: CurrentPrincipal, db: DbSession
) -> Crop:
    field = await _load_field(db, principal, farm_id, field_id)
    return await get_crop(db, field, crop_id)


@router.patch("/{crop_id}", response_model=CropResponse, responses=_RESPONSES)
async def update_crop(
    farm_id: int,
    field_id: int,
    crop_id: int,
    data: CropUpdate,
    principal: CurrentPrincipal,
    db: DbSession,
) -> Crop:
    field = await _load_field(db, principal, farm_id, field_id)
    crop = await get_crop(db, field, crop_id)
    changes = data.changes()

    # Validate the resulting combination, not just the submitted fields.
    try:
        check_planting_date(
            changes.get("status", crop.status),
            data.planting_date or crop.planting_date,
        )
    except ValueError as exc:
        raise unprocessable(str(exc))

    for key, value in changes.items():
        setattr(crop, key, value)
    await db.commit()
    await db.refresh(crop)
    return crop


@router.delete("/{crop_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_RESPONSES)
async def delete_crop(
    farm_id: int, field_id: int, crop_id: int, principal: CurrentPrincipal, db: DbSession
) -> Response:
    field = await _load_field(db, principal, farm_id, field_id)
    crop = await get_crop(db, field, crop_id)
    await db.delete(crop)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -------------------------------------------------------------- growth stages


@router.post(
    "/{crop_id}/stages",
    response_model=StageEventResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_RESPONSES,
)
async def record_stage(
    farm_id: int,
    field_id: int,
    crop_id: int,
    data: StageEventCreate,
    principal: CurrentPrincipal,
    db: DbSession,
) -> CropStageEvent:
    """Record an observed growth stage. The most recent observation becomes
    the crop's current stage."""

    field = await _load_field(db, principal, farm_id, field_id)
    crop = await get_crop(db, field, crop_id)
    try:
        check_stage(crop.crop_type, data.stage)
    except ValueError as exc:
        raise unprocessable(str(exc))
    if data.observed_on < crop.planting_date:
        raise unprocessable("Observation date is before the planting date.")

    event = CropStageEvent(
        crop_id=crop.id,
        stage=data.stage,
        observed_on=data.observed_on,
        notes=data.notes,
        recorded_by=principal.user_id,
    )
    db.add(event)
    await db.flush()

    # Back-dated observations must not overwrite a more recent current stage.
    latest = await db.scalar(
        select(CropStageEvent)
        .where(CropStageEvent.crop_id == crop.id)
        .order_by(CropStageEvent.observed_on.desc(), CropStageEvent.id.desc())
        .limit(1)
    )
    crop.growth_stage = latest.stage if latest else data.stage

    await db.commit()
    await db.refresh(event)
    return event


@router.get("/{crop_id}/stages", response_model=list[StageEventResponse], responses=_RESPONSES)
async def list_stages(
    farm_id: int, field_id: int, crop_id: int, principal: CurrentPrincipal, db: DbSession
) -> list[CropStageEvent]:
    """Growth stage history, oldest first."""
    field = await _load_field(db, principal, farm_id, field_id)
    crop = await get_crop(db, field, crop_id)
    return list(
        await db.scalars(
            select(CropStageEvent)
            .where(CropStageEvent.crop_id == crop.id)
            .order_by(CropStageEvent.observed_on, CropStageEvent.id)
        )
    )
