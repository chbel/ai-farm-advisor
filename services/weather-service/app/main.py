import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.weather import router as weather_router
from app.core.config import settings
from app.services.cache import TTLCache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
# httpx logs full request URLs at INFO, which would include the provider API key.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("weather_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(follow_redirects=False)
    app.state.cache = TTLCache(settings.CACHE_MAX_ENTRIES)
    logger.info("Weather Service starting (environment=%s)", settings.ENVIRONMENT)
    yield
    await app.state.http_client.aclose()
    logger.info("Weather Service shut down")


app = FastAPI(
    title="AI Farm Advisor - Weather Service",
    description=(
        "Current weather, forecasts, history, agricultural indicators (ET0, growing "
        "degree days, water balance) and rule-based crop weather alerts. "
        "Weather data by Open-Meteo.com (CC BY 4.0)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(weather_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness. The provider is not called here, to save its quota."""
    return {"status": "healthy", "service": settings.SERVICE_NAME}
