import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.proxy import router as proxy_router
from app.core.config import settings
from app.core.proxy import REQUEST_ID_HEADER

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("api_gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown operations.

    One shared HTTP client gives connection pooling to every upstream service.
    """

    app.state.http_client = httpx.AsyncClient(
        timeout=settings.UPSTREAM_TIMEOUT_SECONDS,
        follow_redirects=False,
    )
    logger.info("API Gateway starting (environment=%s)", settings.ENVIRONMENT)

    yield

    await app.state.http_client.aclose()
    logger.info("API Gateway shut down")


# Initialize the FastAPI application using centralized settings.
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Central entry point for AI Farm Advisor microservices.",
    lifespan=lifespan,
)


# Configure CORS to control which frontend origins can access our API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to clients."""

    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


app.include_router(health_router)
app.include_router(proxy_router)


@app.get("/", tags=["Root"])
async def root():
    """
    Provide a basic welcome response and API documentation location.
    """

    return {
        "message": "Welcome to AI Farm Advisor",
        "documentation": "/docs",
    }
