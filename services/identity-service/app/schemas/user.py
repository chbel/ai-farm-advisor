from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegister(BaseModel):
    """Validate the data required to create a new account."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=150)


class UserLogin(BaseModel):
    """Validate user login credentials."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Define the public user information returned by the API."""

    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: datetime

    # Allow Pydantic to read data directly from SQLAlchemy objects.
    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """Define the structure of a successful authentication response."""

    access_token: str
    token_type: str = "bearer"