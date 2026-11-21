import asyncio
import app.core.platform
from sqlalchemy import text
from app.core.database import engine


async def main():
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT current_database(), current_user")
            )
            print("DATABASE CONNECTED:", result.fetchone())
    finally:
        await engine.dispose()


asyncio.run(main())