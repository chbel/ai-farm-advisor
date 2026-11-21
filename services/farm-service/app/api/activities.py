from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentPrincipal
from app.models import Crop, FarmActivity, Field
from app.models.enums import ActivityType
from app.schemas.farm import ActivityCreate, ActivityList, ActivityResponse
from app.services.access import get_farm, not_found, unprocessable

router = APIRouter(prefix="/farms/{farm_id}/activities", tags=["Farm journal"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
_RESPONSES = {
    401: {"description": "Missing or invalid token."},
    404: {"description": "Farm or activity not found."},
}


@router.post("", response_model=ActivityResponse, status_code=status.HTTP_201_CREATED, responses=_RESPONSES)
async def create_activity(
    farm_id: int, data: ActivityCreate, principal: CurrentPrincipal, db: DbSession
) -> FarmActivity:
    """Record an operation (irrigation, fertilization, treatment, harvest...)."""

    farm = await get_farm(db, principal, farm_id)

    # Optional field / crop references must belong to this farm.
    field_id = data.field_id
    if field_id is not None:
        field = await db.get(Field, field_id)
        if field is None or field.farm_id != farm.id:
            raise unprocessable("field_id does not belong to this farm.")
    if data.crop_id is not None:
        crop = await db.get(Crop, data.crop_id)
        crop_field = await db.get(Field, crop.field_id) if crop else None
        if crop_field is None or crop_field.farm_id != farm.id:
            raise unprocessable("crop_id does not belong to this farm.")
        if field_id is not None and crop_field.id != field_id:
            raise unprocessable("crop_id is not on the given field_id.")
        field_id = crop_field.id  # a crop implies its field

    activity = FarmActivity(
        farm_id=farm.id,
        recorded_by=principal.user_id,
        **{**data.model_dump(mode="json"), "field_id": field_id},
    )
    db.add(activity)
    await db.commit()
    await db.refresh(activity)
    return activity


@router.get("", response_model=ActivityList, responses=_RESPONSES)
async def list_activities(
    farm_id: int,
    principal: CurrentPrincipal,
    db: DbSession,
    activity_type: ActivityType | None = None,
    field_id: int | None = None,
    crop_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActivityList:
    """Farm journal, most recent first, with optional filters."""

    farm = await get_farm(db, principal, farm_id)
    query = select(FarmActivity).where(FarmActivity.farm_id == farm.id)
    if activity_type is not None:
        query = query.where(FarmActivity.activity_type == activity_type.value)
    if field_id is not None:
        query = query.where(FarmActivity.field_id == field_id)
    if crop_id is not None:
        query = query.where(FarmActivity.crop_id == crop_id)
    if date_from is not None:
        query = query.where(FarmActivity.performed_on >= date_from)
    if date_to is not None:
        query = query.where(FarmActivity.performed_on <= date_to)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = await db.scalars(
        query.order_by(FarmActivity.performed_on.desc(), FarmActivity.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return ActivityList(
        items=[ActivityResponse.model_validate(a) for a in items],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.delete("/{activity_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_RESPONSES)
async def delete_activity(
    farm_id: int, activity_id: int, principal: CurrentPrincipal, db: DbSession
) -> Response:
    farm = await get_farm(db, principal, farm_id)
    activity = await db.get(FarmActivity, activity_id)
    if activity is None or activity.farm_id != farm.id:
        raise not_found("Activity")
    await db.delete(activity)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
