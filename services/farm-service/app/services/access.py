"""Ownership-aware loading of farm resources and cross-entity business rules.

Every lookup is scoped by the caller: a farm that exists but belongs to
someone else is reported as 404 (not 403) so ids cannot be probed.
Administrators can access every farm.
"""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.models import Crop, Farm, Field

# Float tolerance when comparing hectare sums.
_AREA_EPSILON = 1e-6


def not_found(entity: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} not found.")


def unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def get_farm(db: AsyncSession, principal: Principal, farm_id: int) -> Farm:
    farm = await db.get(Farm, farm_id)
    if farm is None or (farm.owner_id != principal.user_id and not principal.is_admin):
        raise not_found("Farm")
    return farm


async def get_field(db: AsyncSession, farm: Farm, field_id: int) -> Field:
    field = await db.get(Field, field_id)
    if field is None or field.farm_id != farm.id:
        raise not_found("Field")
    return field


async def get_crop(db: AsyncSession, field: Field, crop_id: int) -> Crop:
    crop = await db.get(Crop, crop_id)
    if crop is None or crop.field_id != field.id:
        raise not_found("Crop")
    return crop


async def allocated_area(db: AsyncSession, farm_id: int, exclude_field_id: int | None = None) -> float:
    """Sum of field areas on a farm, optionally ignoring one field."""
    query = select(func.coalesce(func.sum(Field.area_ha), 0.0)).where(Field.farm_id == farm_id)
    if exclude_field_id is not None:
        query = query.where(Field.id != exclude_field_id)
    return float(await db.scalar(query) or 0.0)


async def ensure_fields_fit_farm(
    db: AsyncSession,
    farm: Farm,
    field_area: float,
    exclude_field_id: int | None = None,
) -> None:
    """Fields may not add up to more than the farm's declared total area."""
    if farm.total_area_ha is None:
        return
    used = await allocated_area(db, farm.id, exclude_field_id)
    if used + field_area > farm.total_area_ha + _AREA_EPSILON:
        available = max(farm.total_area_ha - used, 0.0)
        raise unprocessable(
            f"Field area exceeds the farm's remaining area ({available:g} ha available "
            f"of {farm.total_area_ha:g} ha)."
        )
