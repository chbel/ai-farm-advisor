"""Request / response schemas for the Farm Service."""

from datetime import date, datetime, timedelta
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.enums import (
    STAGES_BY_CROP,
    ActivityType,
    CropStatus,
    CropType,
    Governorate,
    IrrigationMethod,
    SoilTexture,
)

NAME = Field(min_length=1, max_length=120)
NOTES = Field(default=None, max_length=2000)
AREA_HA = Field(gt=0, le=100_000, description="Hectares.")
LATITUDE = Field(ge=-90, le=90, description="WGS84 decimal degrees.")
LONGITUDE = Field(ge=-180, le=180, description="WGS84 decimal degrees.")

# A planned crop may be scheduled up to this far ahead.
MAX_PLANNING_HORIZON = timedelta(days=730)


class PatchModel(BaseModel):
    """Base for PATCH bodies: unknown fields are rejected and required
    database columns cannot be cleared with an explicit null."""

    model_config = ConfigDict(extra="forbid")
    NOT_NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _reject_null_for_required(self):
        cleared = {
            name for name in self.model_fields_set
            if getattr(self, name) is None and name in self.NOT_NULLABLE
        }
        if cleared:
            raise ValueError(f"These fields cannot be null: {', '.join(sorted(cleared))}")
        return self

    def changes(self) -> dict:
        """Only the fields the client sent, enums as plain strings."""
        return self.model_dump(mode="json", exclude_unset=True)


def check_stage(crop_type: CropType | str, stage: str | None) -> None:
    if stage is None:
        return
    allowed = STAGES_BY_CROP[CropType(crop_type)]
    if stage not in {s.value for s in allowed}:
        raise ValueError(
            f"Stage '{stage}' is not valid for {crop_type}. "
            f"Allowed: {', '.join(s.value for s in allowed)}"
        )


def check_planting_date(status: CropStatus | str, planting_date: date) -> None:
    today = date.today()
    if CropStatus(status) == CropStatus.PLANNED:
        if planting_date > today + MAX_PLANNING_HORIZON:
            raise ValueError("Planned planting date is more than two years ahead.")
    elif planting_date > today:
        raise ValueError("Planting date is in the future; use status 'planned'.")


def not_in_future(value: date | None, label: str) -> None:
    if value is not None and value > date.today():
        raise ValueError(f"{label} cannot be in the future.")


# --------------------------------------------------------------------- farms


class FarmCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = NAME
    governorate: Governorate | None = None
    latitude: float = LATITUDE
    longitude: float = LONGITUDE
    total_area_ha: float | None = Field(default=None, gt=0, le=100_000)
    notes: str | None = NOTES


class FarmUpdate(PatchModel):
    NOT_NULLABLE = frozenset({"name", "latitude", "longitude"})

    name: str | None = Field(default=None, min_length=1, max_length=120)
    governorate: Governorate | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    total_area_ha: float | None = Field(default=None, gt=0, le=100_000)
    notes: str | None = NOTES


class FarmResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_id: int
    name: str
    governorate: Governorate | None
    latitude: float
    longitude: float
    total_area_ha: float | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class FarmList(BaseModel):
    items: list[FarmResponse]
    total: int
    limit: int
    offset: int


# -------------------------------------------------------------------- fields


class FieldCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = NAME
    area_ha: float = AREA_HA
    soil_texture: SoilTexture = SoilTexture.UNKNOWN
    irrigation_method: IrrigationMethod


class FieldUpdate(PatchModel):
    NOT_NULLABLE = frozenset({"name", "area_ha", "soil_texture", "irrigation_method"})

    name: str | None = Field(default=None, min_length=1, max_length=120)
    area_ha: float | None = Field(default=None, gt=0, le=100_000)
    soil_texture: SoilTexture | None = None
    irrigation_method: IrrigationMethod | None = None


class FieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    farm_id: int
    name: str
    area_ha: float
    soil_texture: SoilTexture
    irrigation_method: IrrigationMethod
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------- crops


class CropCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crop_type: CropType
    variety: str | None = Field(default=None, max_length=80)
    planting_date: date
    growth_stage: str | None = Field(
        default=None, description="Must be a stage of this crop type (see /farms/catalog)."
    )
    status: CropStatus = CropStatus.ACTIVE
    plant_count: int | None = Field(default=None, gt=0, description="Trees or plants.")
    notes: str | None = NOTES

    @model_validator(mode="after")
    def _agronomic_rules(self):
        check_stage(self.crop_type, self.growth_stage)
        check_planting_date(self.status, self.planting_date)
        if self.status == CropStatus.PLANNED and self.growth_stage is not None:
            raise ValueError("A planned crop cannot have a growth stage yet.")
        return self


class CropUpdate(PatchModel):
    """Crop type is immutable; growth stage changes go through /stages (history)."""

    NOT_NULLABLE = frozenset({"planting_date", "status"})

    variety: str | None = Field(default=None, max_length=80)
    planting_date: date | None = None
    status: CropStatus | None = None
    plant_count: int | None = Field(default=None, gt=0)
    notes: str | None = NOTES


class CropResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    field_id: int
    crop_type: CropType
    variety: str | None
    planting_date: date
    growth_stage: str | None
    status: CropStatus
    plant_count: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def days_since_planting(self) -> int | None:
        """Days since planting; null for crops not planted yet."""
        days = (date.today() - self.planting_date).days
        return days if days >= 0 else None


class StageEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    observed_on: date = Field(default_factory=date.today)
    notes: str | None = NOTES

    @model_validator(mode="after")
    def _not_future(self):
        not_in_future(self.observed_on, "Observation date")
        return self


class StageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    crop_id: int
    stage: str
    observed_on: date
    notes: str | None
    recorded_by: int
    created_at: datetime


# ---------------------------------------------------------------- activities


class ActivityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_type: ActivityType
    performed_on: date
    field_id: int | None = None
    crop_id: int | None = None
    quantity: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20, description="e.g. m3, mm, kg, L")
    product: str | None = Field(default=None, max_length=120)
    notes: str | None = NOTES

    @model_validator(mode="after")
    def _rules(self):
        not_in_future(self.performed_on, "Activity date")
        if self.quantity is not None and not self.unit:
            raise ValueError("A unit is required when a quantity is given.")
        return self


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    farm_id: int
    field_id: int | None
    crop_id: int | None
    activity_type: ActivityType
    performed_on: date
    quantity: float | None
    unit: str | None
    product: str | None
    notes: str | None
    recorded_by: int
    created_at: datetime


class ActivityList(BaseModel):
    items: list[ActivityResponse]
    total: int
    limit: int
    offset: int
