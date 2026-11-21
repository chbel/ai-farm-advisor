from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.models.user import User, UserRole
from app.schemas.user import (
    AdminUserUpdate,
    PasswordChange,
    UserList,
    UserResponse,
    UserUpdate,
)
from app.services.auth import hash_password, verify_password

router = APIRouter(prefix="/users", tags=["Users"])

_AUTH_RESPONSES = {401: {"description": "Missing, invalid or expired token."}}
_ADMIN_RESPONSES = {**_AUTH_RESPONSES, 403: {"description": "Administrators only."}}


# ---------------------------------------------------------------- own profile


@router.get("/me", response_model=UserResponse, responses=_AUTH_RESPONSES)
async def read_my_profile(user: CurrentUser) -> User:
    """Return the authenticated user's profile."""
    return user


@router.patch("/me", response_model=UserResponse, responses=_AUTH_RESPONSES)
async def update_my_profile(changes: UserUpdate, user: CurrentUser, db: DbSession) -> User:
    """Update the authenticated user's name and/or preferred language."""

    # mode="json" turns enums into their plain string values for the database.
    for field, value in changes.model_dump(mode="json", exclude_none=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_AUTH_RESPONSES, 400: {"description": "Current password is incorrect."}},
)
async def change_my_password(data: PasswordChange, user: CurrentUser, db: DbSession) -> Response:
    """Change the password after re-checking the current one."""

    is_valid, _ = verify_password(data.current_password, user.hashed_password)
    if not is_valid:
        # 400, not 401: the token is valid, only the submitted value is wrong.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    user.hashed_password = hash_password(data.new_password)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------- administration


@router.get("", response_model=UserList, responses=_ADMIN_RESPONSES)
async def list_users(
    _: AdminUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    """List all accounts, newest first (administrators only)."""

    total = await db.scalar(select(func.count()).select_from(User))
    users = await db.scalars(
        select(User).order_by(User.id.desc()).limit(limit).offset(offset)
    )
    return UserList(
        items=[UserResponse.model_validate(u) for u in users],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    responses={**_ADMIN_RESPONSES, 404: {"description": "User not found."}},
)
async def admin_update_user(
    user_id: int,
    changes: AdminUserUpdate,
    admin: AdminUser,
    db: DbSession,
) -> User:
    """Change a user's role or active status (administrators only)."""

    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    # Prevent an administrator from locking themselves out by accident.
    if target.id == admin.id and (
        changes.is_active is False
        or (changes.role is not None and changes.role != UserRole.ADMIN)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrators cannot deactivate or demote themselves.",
        )

    if changes.role is not None:
        target.role = changes.role.value
    if changes.is_active is not None:
        target.is_active = changes.is_active
    await db.commit()
    await db.refresh(target)
    return target
