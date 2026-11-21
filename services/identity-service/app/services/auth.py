from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from app.core.config import settings


# Use Argon2 through pwdlib for secure password hashing.
password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Hash a password before storing it in the database."""
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a login password against its stored hash."""
    return password_hash.verify(password, hashed_password)


def create_access_token(user_id: int) -> str:
    """Generate a signed JWT containing the user's ID and expiration."""

    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    # The subject identifies the authenticated user.
    payload = {
        "sub": str(user_id),
        "exp": expires_at,
    }

    # Sign the token using the secret key from the environment.
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )