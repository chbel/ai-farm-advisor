"""Verification of access tokens issued by the Identity Service.

The Farm Service never trusts the gateway blindly: it re-verifies every token
(signature, expiry, issuer, audience, type) and takes the user id and role
from the verified claims.
"""

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

_bearer = HTTPBearer(auto_error=False, description="JWT from POST /api/v1/auth/login")

ADMIN_ROLE = "admin"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    user_id: int
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized("Not authenticated.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY.get_secret_value(),
            # Fixed list: never let the token header choose the algorithm.
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
        if claims.get("type") != "access":
            raise ValueError("not an access token")
        return Principal(user_id=int(claims["sub"]), role=str(claims.get("role", "")))
    except (jwt.PyJWTError, ValueError, TypeError):
        raise _unauthorized("Invalid or expired token.")


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]
