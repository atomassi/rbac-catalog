"""Health check routes."""

from __future__ import annotations

from fastapi import APIRouter

from azurerbac import __version__
from azurerbac.web.routes.models import HealthResponse, VersionResponse

router = APIRouter(tags=["health"])


@router.api_route("/healthz", methods=["GET", "HEAD"])
async def healthz() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(ok=True)


@router.api_route("/version", methods=["GET", "HEAD"])
async def version() -> VersionResponse:
    """Version endpoint."""
    return VersionResponse(version=__version__)
