"""Registry of the microservices reachable through the gateway.

Adding a service = adding one entry to `SERVICES` (plus its URL setting).
Public path:   /api/v1/<public_prefix>/<rest>
Forwarded to:  <base_url>/<upstream_prefix>/<rest>

Every route requires a valid access token at the gateway, except the
(method, <rest>) pairs listed in `public_routes`.
"""

from dataclasses import dataclass, field

from app.core.config import settings


@dataclass(frozen=True)
class UpstreamService:
    name: str
    base_url: str
    public_prefix: str
    upstream_prefix: str
    public_routes: frozenset[tuple[str, str]] = field(default_factory=frozenset)

    def is_public(self, method: str, path: str) -> bool:
        return (method.upper(), path.strip("/")) in self.public_routes


_IDENTITY_URL = str(settings.IDENTITY_SERVICE_URL).rstrip("/")
_FARM_URL = str(settings.FARM_SERVICE_URL).rstrip("/")

SERVICES: tuple[UpstreamService, ...] = (
    UpstreamService(
        name="identity-service",
        base_url=_IDENTITY_URL,
        public_prefix="auth",
        upstream_prefix="auth",
        public_routes=frozenset({("POST", "register"), ("POST", "login")}),
    ),
    UpstreamService(
        name="identity-service",
        base_url=_IDENTITY_URL,
        public_prefix="users",
        upstream_prefix="users",
    ),
    UpstreamService(
        name="farm-service",
        base_url=_FARM_URL,
        public_prefix="farms",
        upstream_prefix="farms",
    ),
)


def unique_services() -> dict[str, str]:
    """Service name -> base URL, one entry per service (for health checks)."""
    return {s.name: s.base_url for s in SERVICES}
