"""Farm Service database models.

Ownership: `Farm.owner_id` is the Identity Service user id (taken from the
verified access token). There is no cross-service foreign key: each service
owns its database. Children (fields, crops, stage events, activities) are
deleted together with their farm.
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import (
    ALL_STAGES,
    ActivityType,
    CropStatus,
    CropType,
    Governorate,
    IrrigationMethod,
    SoilTexture,
    in_values,
)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Farm(TimestampMixin, Base):
    __tablename__ = "farms"
    __table_args__ = (
        CheckConstraint(in_values("governorate", Governorate), name="ck_farms_governorate"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="ck_farms_latitude"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="ck_farms_longitude"),
        CheckConstraint("total_area_ha > 0", name="ck_farms_total_area"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    governorate: Mapped[str | None] = mapped_column(String(20))
    # WGS84 decimal degrees. Used by the Weather Service for forecasts.
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    total_area_ha: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)

    fields: Mapped[list["Field"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan", passive_deletes=True
    )


class Field(TimestampMixin, Base):
    __tablename__ = "fields"
    __table_args__ = (
        CheckConstraint("area_ha > 0", name="ck_fields_area"),
        CheckConstraint(in_values("soil_texture", SoilTexture), name="ck_fields_soil_texture"),
        CheckConstraint(
            in_values("irrigation_method", IrrigationMethod), name="ck_fields_irrigation_method"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    area_ha: Mapped[float] = mapped_column(Float, nullable=False)
    soil_texture: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=SoilTexture.UNKNOWN.value
    )
    irrigation_method: Mapped[str] = mapped_column(String(20), nullable=False)

    farm: Mapped[Farm] = relationship(back_populates="fields")
    crops: Mapped[list["Crop"]] = relationship(
        back_populates="field", cascade="all, delete-orphan", passive_deletes=True
    )


class Crop(TimestampMixin, Base):
    """A planting of one crop on a field (an orchard block or a tomato season)."""

    __tablename__ = "crops"
    __table_args__ = (
        CheckConstraint(in_values("crop_type", CropType), name="ck_crops_crop_type"),
        CheckConstraint(in_values("status", CropStatus), name="ck_crops_status"),
        CheckConstraint(in_values("growth_stage", ALL_STAGES), name="ck_crops_growth_stage"),
        CheckConstraint("plant_count IS NULL OR plant_count > 0", name="ck_crops_plant_count"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), index=True, nullable=False
    )
    crop_type: Mapped[str] = mapped_column(String(20), nullable=False)
    variety: Mapped[str | None] = mapped_column(String(80))  # e.g. Chemlali, Chetoui
    planting_date: Mapped[date] = mapped_column(Date, nullable=False)
    growth_stage: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    plant_count: Mapped[int | None] = mapped_column(Integer)  # trees or plants
    notes: Mapped[str | None] = mapped_column(Text)

    field: Mapped[Field] = relationship(back_populates="crops")
    stage_events: Mapped[list["CropStageEvent"]] = relationship(
        back_populates="crop",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CropStageEvent.observed_on",
    )


class CropStageEvent(Base):
    """History of observed growth stages (who saw which stage, and when)."""

    __tablename__ = "crop_stage_events"
    __table_args__ = (
        CheckConstraint(in_values("stage", ALL_STAGES), name="ck_crop_stage_events_stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    crop_id: Mapped[int] = mapped_column(
        ForeignKey("crops.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    observed_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    crop: Mapped[Crop] = relationship(back_populates="stage_events")


class FarmActivity(Base):
    """Farm journal entry: irrigation, fertilization, treatment, harvest, ..."""

    __tablename__ = "farm_activities"
    __table_args__ = (
        CheckConstraint(in_values("activity_type", ActivityType), name="ck_activities_type"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_activities_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Optional scope inside the farm. SET NULL keeps the journal entry if the
    # field or crop is later deleted.
    field_id: Mapped[int | None] = mapped_column(ForeignKey("fields.id", ondelete="SET NULL"))
    crop_id: Mapped[int | None] = mapped_column(ForeignKey("crops.id", ondelete="SET NULL"))
    activity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    performed_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quantity: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(20))  # e.g. m3, kg, L, mm
    product: Mapped[str | None] = mapped_column(String(120))  # fertilizer / pesticide name
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
