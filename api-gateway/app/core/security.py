"""Access-token verification at the edge.

The gateway rejects missing/invalid tokens early so unauthenticated traffic
never reaches the services. Services still verify tokens themselves
(defence in depth); the gateway only forwards the original Authorization header.
"""

import jwt
from fastapi import HTTPException, Request, status

from app.core.config import settings


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_valid_token(request: Request) -> dict:
    """Return the verified claims of the request's bearer token, or raise 401."""

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Not authenticated.")

    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            # Fixed list: never let the token header choose the algorithm.
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError:
        raise _unauthorized("Invalid or expired token.")

    if claims.get("type") != "access":
        raise _unauthorized("Invalid or expired token.")
    return claims
