import asyncio

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.proxy import get_http_client
from app.core.services import SERVICES, UpstreamService


# APIRouter allows us to organize endpoints separately from main.py.
router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check():
    """
    Liveness: the gateway process is up and answering HTTP requests.
    """

    return {
        "status": "healthy",
        "service": "api-gateway",
        "environment": settings.ENVIRONMENT,
        "version": settings.APP_VERSION,
    }


async def _probe(client: httpx.AsyncClient, service: UpstreamService) -> str:
    try:
        response = await client.get(f"{service.base_url}/health", timeout=3.0)
    except httpx.HTTPError:
        return "unavailable"
    return "healthy" if response.status_code == 200 else "unhealthy"


@router.get("/health/ready")
async def readiness_check(client: httpx.AsyncClient = Depends(get_http_client)):
    """
    Readiness: every upstream microservice answers its /health endpoint.

    Returns 503 when any service is down, so orchestrators and humans can see
    exactly which dependency is failing.
    """

    results = await asyncio.gather(*(_probe(client, s) for s in SERVICES))
    services = {s.name: r for s, r in zip(SERVICES, results)}
    ready = all(r == "healthy" for r in results)

    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "services": services},
    )
