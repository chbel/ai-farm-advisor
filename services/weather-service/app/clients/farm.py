"""Farm Service client: resolves a farm's location on behalf of the caller.

The caller's own Authorization header is forwarded, so the Farm Service
enforces ownership. This service never sees farms the user cannot see.
"""

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.weather import FarmRef


async def get_farm(http: httpx.AsyncClient, farm_id: int, authorization: str) -> FarmRef:
    url = f"{str(settings.FARM_SERVICE_URL).rstrip('/')}/farms/{farm_id}"
    try:
        response = await http.get(url, headers={"Authorization": authorization}, timeout=5.0)
    except httpx.RequestError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="farm-service is currently unavailable.",
        )

    if response.status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farm not found.")
    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="farm-service returned an unexpected response.",
        )
    return FarmRef.model_validate(response.json())
