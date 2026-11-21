"""Reverse-proxy logic: forward an incoming request to an upstream service."""

import logging
import uuid

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from app.core.services import UpstreamService

logger = logging.getLogger("api_gateway.proxy")

REQUEST_ID_HEADER = "x-request-id"

# Hop-by-hop headers (RFC 9110 §7.6.1) must not be forwarded. `host` and
# `content-length` are recomputed by httpx for the upstream request.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_DROP_REQUEST_HEADERS = _HOP_BY_HOP | {"host", "content-length"}
# httpx already decoded the body, so the original encoding/length no longer apply.
_DROP_RESPONSE_HEADERS = _HOP_BY_HOP | {"content-length", "content-encoding"}


def get_http_client(request: Request) -> httpx.AsyncClient:
    """Shared connection-pooled client created in the app lifespan."""
    return request.app.state.http_client


async def forward(
    request: Request,
    service: UpstreamService,
    path: str,
    client: httpx.AsyncClient,
) -> Response:
    """Forward `request` to `service` and relay its response unchanged."""

    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _DROP_REQUEST_HEADERS
    }
    headers[REQUEST_ID_HEADER] = request_id
    if request.client is not None:
        headers["x-forwarded-for"] = request.client.host

    url = f"{service.base_url}/{service.upstream_prefix}/{path}".rstrip("/")

    try:
        upstream = await client.request(
            method=request.method,
            url=url,
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
    except httpx.TimeoutException:
        logger.warning("Timeout from %s (request_id=%s)", service.name, request_id)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"{service.name} did not respond in time.",
        )
    except httpx.RequestError as exc:
        # Log the error class only: messages can contain internal addresses.
        logger.error(
            "Cannot reach %s: %s (request_id=%s)",
            service.name,
            type(exc).__name__,
            request_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{service.name} is currently unavailable.",
        )

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _DROP_RESPONSE_HEADERS
    }
    response_headers[REQUEST_ID_HEADER] = request_id

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
    )
