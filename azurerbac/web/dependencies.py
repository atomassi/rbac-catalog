"""Dependency injection for FastAPI route handlers.

This module provides typed dependency containers and FastAPI dependency
functions to inject shared resources into route handlers without using
global variables.

Usage in route handlers:
    from azurerbac.web.dependencies import get_api_deps, APIDeps

    @router.get("/some-endpoint")
    async def endpoint(deps: APIDeps = Depends(get_api_deps)):
        await deps.get_all_operations()
        deps.app_cache.search_operations(...)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from fastapi.templating import Jinja2Templates
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from azurerbac.azure.models import OperationData, RoleDefinition
    from azurerbac.cache import AppCache
    from azurerbac.core.models import Operation, Role, RoleHistory, RoleScanStatus


@dataclass(slots=True)
class BaseDeps:
    """Common dependencies shared across all route types."""

    app_cache: AppCache
    SessionLocal: async_sessionmaker[AsyncSession]


@dataclass(slots=True)
class APIDeps(BaseDeps):
    """Dependencies for API routes (/api/*).

    Provides cache access and service functions for API endpoints.
    """

    get_all_operations: Callable[[], Awaitable[list[OperationData]]]
    get_all_roles: Callable[[], Awaitable[list[RoleDefinition]]]


@dataclass(slots=True)
class DashboardDeps(BaseDeps):
    """Dependencies for dashboard routes (/, /recent, /roles).

    Provides models, templates, and RoleScanStatus for dashboard pages.
    """

    Role: type[Role]
    RoleHistory: type[RoleHistory]
    RoleScanStatus: type[RoleScanStatus]
    Operation: type[Operation]
    templates: Jinja2Templates


@dataclass(slots=True)
class PagesDeps(BaseDeps):
    """Dependencies for page routes (/roles/{id}, /operations, etc).

    Provides models, templates, and operations service.
    """

    Role: type[Role]
    RoleHistory: type[RoleHistory]
    Operation: type[Operation]
    templates: Jinja2Templates
    get_all_operations: Callable[[], Awaitable[list[OperationData]]]


def get_api_deps(request: Request) -> APIDeps:
    """FastAPI dependency that returns API dependencies from app.state.

    Updates SessionLocal from app.state.session_local to support test patching.
    """
    deps = request.app.state.api_deps
    # Use session_local from app.state (allows test patching in one place)
    deps.SessionLocal = request.app.state.session_local
    return deps


def get_dashboard_deps(request: Request) -> DashboardDeps:
    """FastAPI dependency that returns dashboard dependencies from app.state.

    Updates SessionLocal from app.state.session_local to support test patching.
    """
    deps = request.app.state.dashboard_deps
    # Use session_local from app.state (allows test patching in one place)
    deps.SessionLocal = request.app.state.session_local
    return deps


def get_pages_deps(request: Request) -> PagesDeps:
    """FastAPI dependency that returns pages dependencies from app.state.

    Updates SessionLocal from app.state.session_local to support test patching.
    """
    deps = request.app.state.pages_deps
    # Use session_local from app.state (allows test patching in one place)
    deps.SessionLocal = request.app.state.session_local
    return deps
