# Configure the Windows event loop before creating an async connection.
import app.core.platform

import asyncio

from app.core.database import Base, engine
from app.models.user import User


async def init_database():
    """Create database tables that do not already exist."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    await engine.dispose()
    print("Identity database initialized successfully.")


if __name__ == "__main__":
    asyncio.run(init_database())