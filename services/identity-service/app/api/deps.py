"""Reusable FastAPI dependencies: database session and authenticated user."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User, UserRole
from app.services.auth import InvalidTokenError, decode_access_token

# auto_error=False so a missing header yields 401 (not FastAPI's default 403).
_bearer = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the bearer token to an active user, or fail with 401."""

    if credentials is None:
        raise _unauthorized("Not authenticated.")
    try:
        claims = decode_access_token(credentials.credentials)
    except InvalidTokenError:
        # The reason (expired, bad signature...) is not disclosed to the client.
        raise _unauthorized("Invalid or expired token.")

    user = await db.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    """Allow only administrators. The role is read from the database, not the token."""

    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]
