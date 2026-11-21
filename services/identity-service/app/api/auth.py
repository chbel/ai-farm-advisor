from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.schemas.user import UserRegister, UserLogin, UserResponse, TokenResponse
from app.services.auth import (
    hash_password,
    verify_password,
    create_access_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_user(
    user_data: UserRegister,
    db: AsyncSession = Depends(get_db),
):
    """Create a new user account."""

    email = str(user_data.email).lower()

    # Check whether this email is already registered.
    result = await db.execute(
        select(User).where(User.email == email)
    )
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    # Hash the password before storing it.
    new_user = User(
        email=email,
        full_name=user_data.full_name,
        hashed_password=hash_password(user_data.password),
    )

    db.add(new_user)

    try:
        await db.commit()
        await db.refresh(new_user)
    except IntegrityError:
        # Handle a simultaneous registration using the same email.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return new_user


@router.post("/login", response_model=TokenResponse)
async def login_user(
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate a user and return an access token."""

    email = str(login_data.email).lower()

    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalar_one_or_none()

    # Use the same error for unknown emails and incorrect passwords.
    if not user or not verify_password(
        login_data.password,
        user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is inactive.",
        )

    # Generate a signed JWT for the authenticated user.
    access_token = create_access_token(user.id)

    return TokenResponse(access_token=access_token)