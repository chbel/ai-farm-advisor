import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.irrigation import router as irrigation_router
from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("irrigation_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(follow_redirects=False)
    logger.info("Irrigation Service starting (environment=%s)", settings.ENVIRONMENT)
    yield
    await app.state.http_client.aclose()
    logger.info("Irrigation Service shut down")


app = FastAPI(
    title="AI Farm Advisor - Irrigation Service",
    description=(
        "Crop water requirements and irrigation recommendations following "
        "FAO Irrigation and Drainage Paper 56 (ETc = Kc x ET0, TAW/RAW)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(irrigation_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": settings.SERVICE_NAME}
