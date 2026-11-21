"""Operational commands for the Identity Service.

Promote an existing account to administrator (bootstraps the first admin,
since the API never lets users grant themselves roles):

    python -m app.cli promote-admin user@example.com
    docker compose exec identity-service python -m app.cli promote-admin user@example.com
"""

# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.models.user import User, UserRole


async def promote_admin(email: str) -> int:
    try:
        async with AsyncSessionLocal() as db:
            user = await db.scalar(select(User).where(User.email == email.lower()))
            if user is None:
                print(f"No account found for {email}.", file=sys.stderr)
                return 1
            user.role = UserRole.ADMIN.value
            await db.commit()
            print(f"{user.email} is now an administrator.")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser("promote-admin", help="Grant the admin role to a user.")
    promote.add_argument("email")

    args = parser.parse_args()
    if args.command == "promote-admin":
        return asyncio.run(promote_admin(args.email))
    return 2


if __name__ == "__main__":
    sys.exit(main())
