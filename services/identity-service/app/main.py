# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.core.config import settings
from app.core.database import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("identity_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Identity Service starting (environment=%s)", settings.ENVIRONMENT)
    yield
    await engine.dispose()
    logger.info("Identity Service shut down")


app = FastAPI(
    title="AI Farm Advisor - Identity Service",
    description="User registration, authentication, profiles and roles.",
    version="1.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces, SQL or connection details to clients."""

    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(auth_router)
app.include_router(users_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness: the process is answering requests."""
    return {"status": "healthy", "service": settings.SERVICE_NAME}


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Readiness: the database is reachable."""
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Database readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unavailable"},
        )
    return {"status": "ready", "database": "connected"}
