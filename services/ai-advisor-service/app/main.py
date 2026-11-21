# Must run before any event loop is created (Windows + async Psycopg).
import app.core.platform  # noqa: F401

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from app.agents.graph import build_graph
from app.api.advisor import router as advisor_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.knowledge.embeddings import E5Embedder, download_model
from app.llm.client import build_chat_model
from app.models import KnowledgeChunk

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("advisor")


def load_embedder() -> E5Embedder | None:
    try:
        model_dir = download_model(settings.EMBEDDING_REPO, settings.EMBEDDING_REVISION,
                                   settings.DATA_DIR / "models" / "multilingual-e5-small")
        return E5Embedder(model_dir)
    except Exception:
        logger.exception("Embedding model could not be loaded; knowledge search disabled.")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(follow_redirects=False)
    app.state.sessionmaker = AsyncSessionLocal
    app.state.llm = build_chat_model()
    app.state.embedder = await asyncio.to_thread(load_embedder)
    app.state.graph = build_graph()
    logger.info("AI Advisor starting (LLM providers: %s; knowledge search: %s)",
                ", ".join(p.name for p in app.state.llm.providers) or "none",
                "on" if app.state.embedder else "off")
    yield
    await app.state.http_client.aclose()
    await engine.dispose()


app = FastAPI(
    title="AI Farm Advisor - AI Advisor Service",
    description=(
        "Agricultural assistant orchestrated with LangGraph: farm, weather, irrigation and "
        "disease specialists plus retrieval over a curated, licensed knowledge base. "
        "Answers cite their sources."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak stack traces or internal details to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


app.include_router(advisor_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy", "service": settings.SERVICE_NAME}


@app.get("/health/ready", tags=["Health"])
async def readiness_check(request: Request):
    """Database reachable; reports knowledge base size and LLM availability."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            chunks = await db.scalar(select(func.count()).select_from(KnowledgeChunk))
    except Exception:
        logger.exception("Database readiness check failed")
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})
    return {
        "status": "ready",
        "knowledge_chunks": chunks,
        "search_model": request.app.state.embedder is not None,
        "llm_providers": [p.name for p in request.app.state.llm.providers],
    }
