"""Dependency injection for FastAPI routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from azurerbac.cache import CacheService
    from azurerbac.core.models import Operation, Role, RoleHistory, RoleScanStatus


@dataclass(slots=True)
class BaseDeps:
    """Common dependencies for all routes."""

    app_cache: CacheService
    SessionLocal: async_sessionmaker[AsyncSession]


@dataclass(slots=True)
class DashboardDeps(BaseDeps):
    """Dependencies for dashboard routes."""

    Role: type[Role]
    RoleHistory: type[RoleHistory]
    RoleScanStatus: type[RoleScanStatus]
    Operation: type[Operation]
    templates: Jinja2Templates


@dataclass(slots=True)
class PagesDeps(BaseDeps):
    """Dependencies for page routes."""

    Role: type[Role]
    RoleHistory: type[RoleHistory]
    Operation: type[Operation]
    templates: Jinja2Templates


def _patch_session_local[T: BaseDeps](request: Request, deps: T) -> T:
    deps.SessionLocal = request.app.state.session_local
    return deps


def get_api_deps(request: Request) -> BaseDeps:
    """Get API dependencies from request."""
    return _patch_session_local(request, request.app.state.api_deps)


def get_dashboard_deps(request: Request) -> DashboardDeps:
    """Get dashboard dependencies from request."""
    return _patch_session_local(request, request.app.state.dashboard_deps)


def get_pages_deps(request: Request) -> PagesDeps:
    """Get pages dependencies from request."""
    return _patch_session_local(request, request.app.state.pages_deps)
