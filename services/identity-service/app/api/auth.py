from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbSession
from app.core.config import settings
from app.models.user import User, UserRole
from app.schemas.user import TokenResponse, UserLogin, UserRegister, UserResponse
from app.services.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Authentication"])

_DUPLICATE_EMAIL = "An account with this email already exists."


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": _DUPLICATE_EMAIL}},
)
async def register_user(user_data: UserRegister, db: DbSession) -> User:
    """Create a new farmer account. Other roles are granted by an administrator."""

    email = str(user_data.email).lower()

    # Check whether this email is already registered.
    existing = await db.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_EMAIL)

    # Hash the password before storing it.
    new_user = User(
        email=email,
        full_name=user_data.full_name,
        hashed_password=hash_password(user_data.password),
        role=UserRole.FARMER.value,
        preferred_language=user_data.preferred_language.value,
    )
    db.add(new_user)

    try:
        await db.commit()
    except IntegrityError:
        # Handle a simultaneous registration using the same email.
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_DUPLICATE_EMAIL)

    await db.refresh(new_user)
    return new_user


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"description": "Incorrect email or password."}},
)
async def login_user(login_data: UserLogin, db: DbSession) -> TokenResponse:
    """Authenticate a user and return an access token."""

    email = str(login_data.email).lower()
    user = await db.scalar(select(User).where(User.email == email))

    # Same error and same hashing cost for unknown emails, wrong passwords and
    # inactive accounts, so the endpoint does not reveal which accounts exist.
    is_valid, new_hash = verify_password(
        login_data.password,
        user.hashed_password if user else None,
    )
    if user is None or not is_valid or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Transparently upgrade hashes created with older Argon2 parameters.
    if new_hash is not None:
        user.hashed_password = new_hash
        await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.role),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
