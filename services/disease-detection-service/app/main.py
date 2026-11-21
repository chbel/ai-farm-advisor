import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.disease import router as disease_router
from app.core.config import settings
from app.services.model import load_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("disease_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Models are loaded once at startup; uploads are never stored.
    app.state.models = load_models(settings.MODEL_DIR, settings.ONNX_THREADS)
    logger.info("Disease Detection Service starting with models for: %s",
                ", ".join(app.state.models) or "none")
    yield
    logger.info("Disease Detection Service shut down")


app = FastAPI(
    title="AI Farm Advisor - Disease Detection Service",
    description=(
        "Leaf photo analysis with calibrated confidence. Results are estimates, "
        "not diagnoses. Uploaded images are processed in memory and not stored."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(disease_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness."""
    return {"status": "healthy", "service": settings.SERVICE_NAME}


@app.get("/health/ready", tags=["Health"])
async def readiness_check(request: Request):
    """Ready when at least one model is loaded."""
    models = request.app.state.models
    if not models:
        return JSONResponse(status_code=503, content={"status": "degraded", "models": []})
    return {"status": "ready", "models": {crop: m.version for crop, m in models.items()}}
