from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import CurrentPrincipal
from app.models import Field
from app.schemas.farm import FieldCreate, FieldResponse, FieldUpdate
from app.services.access import ensure_fields_fit_farm, get_farm, get_field

router = APIRouter(prefix="/farms/{farm_id}/fields", tags=["Fields"])

DbSession = Annotated[AsyncSession, Depends(get_db)]
_RESPONSES = {
    401: {"description": "Missing or invalid token."},
    404: {"description": "Farm or field not found."},
}


@router.post("", response_model=FieldResponse, status_code=status.HTTP_201_CREATED, responses=_RESPONSES)
async def create_field(
    farm_id: int, data: FieldCreate, principal: CurrentPrincipal, db: DbSession
) -> Field:
    farm = await get_farm(db, principal, farm_id)
    await ensure_fields_fit_farm(db, farm, data.area_ha)

    field = Field(farm_id=farm.id, **data.model_dump(mode="json"))
    db.add(field)
    await db.commit()
    await db.refresh(field)
    return field


@router.get("", response_model=list[FieldResponse], responses=_RESPONSES)
async def list_fields(farm_id: int, principal: CurrentPrincipal, db: DbSession) -> list[Field]:
    farm = await get_farm(db, principal, farm_id)
    return list(await db.scalars(select(Field).where(Field.farm_id == farm.id).order_by(Field.id)))


@router.get("/{field_id}", response_model=FieldResponse, responses=_RESPONSES)
async def read_field(
    farm_id: int, field_id: int, principal: CurrentPrincipal, db: DbSession
) -> Field:
    farm = await get_farm(db, principal, farm_id)
    return await get_field(db, farm, field_id)


@router.patch("/{field_id}", response_model=FieldResponse, responses=_RESPONSES)
async def update_field(
    farm_id: int, field_id: int, data: FieldUpdate, principal: CurrentPrincipal, db: DbSession
) -> Field:
    farm = await get_farm(db, principal, farm_id)
    field = await get_field(db, farm, field_id)
    changes = data.changes()

    if "area_ha" in changes:
        await ensure_fields_fit_farm(db, farm, changes["area_ha"], exclude_field_id=field.id)

    for key, value in changes.items():
        setattr(field, key, value)
    await db.commit()
    await db.refresh(field)
    return field


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_RESPONSES)
async def delete_field(
    farm_id: int, field_id: int, principal: CurrentPrincipal, db: DbSession
) -> Response:
    """Delete a field and its crops. Journal entries are kept (unlinked)."""
    farm = await get_farm(db, principal, farm_id)
    field = await get_field(db, farm, field_id)
    await db.delete(field)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
