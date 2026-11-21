from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(StrEnum):
    """Platform roles. New accounts are always FARMER; others are granted by an admin."""

    FARMER = "farmer"
    TECHNICIAN = "technician"
    CONSULTANT = "consultant"
    ADMIN = "admin"


class Language(StrEnum):
    """Supported interface languages (ISO 639 codes; `aeb` = Tunisian Arabic / Derja)."""

    FRENCH = "fr"
    ENGLISH = "en"
    TUNISIAN_ARABIC = "aeb"


def _in_values(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return f"{column} IN ({values})"


class User(Base):
    """Database model representing a registered AI Farm Advisor user."""

    __tablename__ = "users"
    # Plain strings + CHECK constraints instead of native PostgreSQL ENUMs:
    # adding a value later is a simple constraint change in a migration.
    __table_args__ = (
        CheckConstraint(_in_values("role", UserRole), name="ck_users_role"),
        CheckConstraint(
            _in_values("preferred_language", Language),
            name="ck_users_preferred_language",
        ),
    )

    # Unique database identifier for each user.
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    # Email is unique because each account must have its own login.
    # Always stored lowercase.
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    # Store only the hashed password, never the original password.
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # User's full name, used for account identification and display.
    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserRole.FARMER.value,
        server_default=UserRole.FARMER.value,
    )

    preferred_language: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=Language.FRENCH.value,
        server_default=Language.FRENCH.value,
    )

    # Inactive accounts cannot authenticate.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Database automatically records when the account is created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
