"""Public /api/v1 routes, generated from the service registry."""

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.core.proxy import forward, get_http_client
from app.core.services import SERVICES, UpstreamService

router = APIRouter(prefix="/api/v1")

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _register(service: UpstreamService) -> None:
    async def proxy_endpoint(
        path: str,
        request: Request,
        client: httpx.AsyncClient = Depends(get_http_client),
    ) -> Response:
        return await forward(request, service, path, client)

    proxy_endpoint.__name__ = f"proxy_{service.public_prefix}"
    router.add_api_route(
        f"/{service.public_prefix}/{{path:path}}",
        proxy_endpoint,
        methods=_METHODS,
        tags=[service.name],
        summary=f"Forward to {service.name}",
        description=(
            f"Forwards /api/v1/{service.public_prefix}/* to {service.name}. "
            f"See that service's own OpenAPI docs for request/response schemas."
        ),
    )


for _service in SERVICES:
    _register(_service)
