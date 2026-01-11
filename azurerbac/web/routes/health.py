"""Health check route handler."""

from __future__ import annotations

from fastapi import APIRouter

from azurerbac import __version__
from azurerbac.web.routes.models import HealthResponse, VersionResponse

router = APIRouter(tags=["health"])


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> HealthResponse:
    """Health check endpoint.

    Returns a simple JSON response indicating the service is healthy.
    Used by:
    - Azure Front Door health probes
    - Cloudflare health probes
    - Container health checks
    - Monitoring systems

    Supports HEAD method for lightweight health checks.
    """
    return HealthResponse(ok=True)


@router.api_route("/version", methods=["GET", "HEAD"])
async def version() -> VersionResponse:
    """Version endpoint.

    Returns the current application version.
    Format: {tag}-{short_sha} e.g., 0.2.7-0e038d1

    Supports HEAD method for lightweight version checks.
    """
    return VersionResponse(version=__version__)
