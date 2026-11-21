"""Password hashing and JWT creation/verification."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings

# Use Argon2 through pwdlib for secure password hashing.
password_hash = PasswordHash.recommended()

# Verified against when the email is unknown, so login takes the same time
# whether or not an account exists (prevents account enumeration by timing).
_DUMMY_HASH = password_hash.hash("dummy-password-for-timing-equalization")

ACCESS_TOKEN_TYPE = "access"


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired, or has invalid claims."""


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    role: str


def hash_password(password: str) -> str:
    """Hash a password before storing it in the database."""
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    """
    Check a password against its stored hash.

    Returns (is_valid, new_hash). `new_hash` is set when the stored hash uses
    outdated parameters and should be replaced. With `hashed_password=None`
    a dummy hash is checked so the call costs the same time.
    """
    if hashed_password is None:
        password_hash.verify(password, _DUMMY_HASH)
        return False, None
    return password_hash.verify_and_update(password, hashed_password)


def create_access_token(user_id: int, role: str) -> str:
    """Generate a signed JWT identifying the user and their role."""

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }

    # Sign the token using the secret key from the environment.
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> TokenClaims:
    """Verify signature, expiry, issuer and audience; return the claims."""

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY.get_secret_value(),
            # Fixed list: never let the token header choose the algorithm.
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Not an access token.")
    try:
        return TokenClaims(user_id=int(payload["sub"]), role=str(payload.get("role", "")))
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError("Invalid subject claim.") from exc
