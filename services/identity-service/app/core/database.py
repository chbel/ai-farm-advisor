from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# Create an asynchronous PostgreSQL connection pool using Psycopg 3.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

# Create independent database sessions for API requests.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""
    pass


async def get_db():
    """Provide a database session and close it after each request."""
    async with AsyncSessionLocal() as session:
        yield session