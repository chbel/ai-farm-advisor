from datetime import datetime

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """Database model representing a registered AI Farm Advisor user."""

    __tablename__ = "users"

    # Unique database identifier for each user.
    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True,
    )

    # Email is unique because each account must have its own login.
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