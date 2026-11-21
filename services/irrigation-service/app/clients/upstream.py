"""Calls to the Farm and Weather services on behalf of the caller.

The caller's Authorization header is forwarded, so ownership is enforced by
the Farm Service; this service never sees data the user cannot see.
"""

import httpx
from fastapi import HTTPException, status

from app.core.config import settings


async def get_json(http: httpx.AsyncClient, service: str, url: str, authorization: str):
    try:
        response = await http.get(
            url,
            headers={"Authorization": authorization},
            timeout=settings.UPSTREAM_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, f"{service} did not respond in time.")
    except httpx.RequestError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{service} is currently unavailable.")

    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Farm or field not found.")
    if response.status_code == 401:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{service} returned an unexpected response.")


def farm_url(path: str) -> str:
    return f"{str(settings.FARM_SERVICE_URL).rstrip('/')}{path}"


def weather_url(path: str) -> str:
    return f"{str(settings.WEATHER_SERVICE_URL).rstrip('/')}{path}"
