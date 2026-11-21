from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import Language, UserRole

# Argon2 handles any length, but an upper bound prevents abuse with huge payloads.
PASSWORD_FIELD = Field(min_length=8, max_length=128)
FULL_NAME_FIELD = Field(min_length=2, max_length=150)


def _strip_name(value: str) -> str:
    return " ".join(value.split())


class UserRegister(BaseModel):
    """Validate the data required to create a new account."""

    email: EmailStr
    password: str = PASSWORD_FIELD
    full_name: str = FULL_NAME_FIELD
    preferred_language: Language = Language.FRENCH

    @field_validator("full_name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return _strip_name(value)


class UserLogin(BaseModel):
    """Validate user login credentials."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserUpdate(BaseModel):
    """Fields a user may change on their own profile (all optional)."""

    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    preferred_language: Language | None = None

    @field_validator("full_name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        return _strip_name(value) if value is not None else None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = PASSWORD_FIELD


class AdminUserUpdate(BaseModel):
    """Fields only an administrator may change."""

    role: UserRole | None = None
    is_active: bool | None = None


class UserResponse(BaseModel):
    """Define the public user information returned by the API."""

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    preferred_language: Language
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # Allow Pydantic to read data directly from SQLAlchemy objects.
    model_config = ConfigDict(from_attributes=True)


class UserList(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int


class TokenResponse(BaseModel):
    """Define the structure of a successful authentication response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token lifetime in seconds.")
