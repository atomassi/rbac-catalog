"""Health check routes."""

from __future__ import annotations

from fastapi import APIRouter

from azurerbac import __version__
from azurerbac.web.routes.models import HealthResponse, VersionResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(ok=True)


@router.head("/healthz", response_model=HealthResponse)
async def healthz_head() -> HealthResponse:
    """Health check endpoint (HEAD)."""
    return HealthResponse(ok=True)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Version endpoint."""
    return VersionResponse(version=__version__)


@router.head("/version", response_model=VersionResponse)
async def version_head() -> VersionResponse:
    """Version endpoint (HEAD)."""
    return VersionResponse(version=__version__)
