"""Public /api/v1 routes, generated from the service registry."""

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.proxy import forward, get_http_client
from app.core.security import require_valid_token
from app.core.services import SERVICES, UpstreamService

router = APIRouter(prefix="/api/v1")

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _register(service: UpstreamService) -> None:
    async def proxy_endpoint(
        request: Request,
        client: httpx.AsyncClient = Depends(get_http_client),
    ) -> Response:
        # Read from the URL path only (never a query parameter); "" on the bare prefix.
        path: str = request.path_params.get("path", "")
        if not service.is_public(request.method, path):
            require_valid_token(request)
        return await forward(request, service, path, client)

    proxy_endpoint.__name__ = f"proxy_{service.public_prefix}"
    common = {
        "endpoint": proxy_endpoint,
        "methods": _METHODS,
        "tags": [service.name],
        "summary": f"Forward to {service.name}",
        "description": (
            f"Forwards /api/v1/{service.public_prefix}/* to {service.name}. "
            f"See that service's own OpenAPI docs for request/response schemas."
        ),
    }
    # Bare prefix (e.g. GET /api/v1/users) and everything below it. Without the
    # first route FastAPI would answer the bare prefix with a 307 redirect.
    router.add_api_route(f"/{service.public_prefix}", **common)
    router.add_api_route(f"/{service.public_prefix}/{{path:path}}", **common)


for _service in SERVICES:
    _register(_service)
