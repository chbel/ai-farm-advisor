"""Registry of the microservices reachable through the gateway.

Adding a service = adding one entry to `SERVICES` (plus its URL setting).
Public path:   /api/v1/<public_prefix>/<rest>
Forwarded to:  <base_url>/<upstream_prefix>/<rest>
"""

from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class UpstreamService:
    name: str
    base_url: str
    public_prefix: str
    upstream_prefix: str


SERVICES: tuple[UpstreamService, ...] = (
    UpstreamService(
        name="identity-service",
        base_url=str(settings.IDENTITY_SERVICE_URL).rstrip("/"),
        public_prefix="auth",
        upstream_prefix="auth",
    ),
)
