from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Asynchronous PostgreSQL connection pool using Psycopg 3.
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all database models."""


async def get_db() -> AsyncIterator[AsyncSession]:
    """Provide a database session and close it after each request."""
    async with AsyncSessionLocal() as session:
        yield session
