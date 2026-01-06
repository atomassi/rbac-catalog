"""Cache data access services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache import AppCache, app_cache
from azurerbac.core import Operation, Role
from azurerbac.core.constants import RoleStatus


@asynccontextmanager
async def _session_factory_or_default(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if session_factory is not None:
        yield session_factory
        return

    from azurerbac.core import EngineFactory, create_sessionmaker

    engine = EngineFactory.from_settings()
    try:
        yield create_sessionmaker(engine)
    finally:
        await engine.dispose()


async def get_all_operations(
    cache: AppCache | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[OperationData]:
    """Get all operations from cache or database.

    Args:
        cache: Optional cache instance (defaults to app_cache singleton)
        session_factory: Optional session factory for database fallback.
            If not provided and cache is empty, returns empty list.

    Returns:
        List of OperationData objects.
    """
    cache = cache or app_cache

    cached = cache.get_all_operations()
    if cached:
        return cached

    # If no session factory provided, just return empty (useful for tests)
    if session_factory is None:
        return []

    async with session_factory() as session:
        result = await session.execute(select(Operation))
        operations = result.scalars().all()

        ops_list = [
            OperationData.model_validate(
                {
                    "name": op.name,
                    "displayName": op.display_name,
                    "description": op.description,
                    "provider_display_name": op.provider_display_name or "",
                    "resource_type_display_name": op.resource_type_display_name,
                    "isDataAction": op.is_data_action,
                }
            )
            for op in operations
        ]
        cache.build_from_operations(ops_list)
        return ops_list


async def get_all_roles(
    cache: AppCache | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[RoleDefinition]:
    """Get all active roles as RoleDefinition objects.

    Args:
        cache: Optional cache instance (defaults to app_cache singleton)
        session_factory: Optional session factory for database fallback.

    Returns:
        List of RoleDefinition Pydantic models for active roles.
    """
    cache = cache or app_cache

    # Try cache first (primary path)
    if cached := cache.get_all_roles():
        return cached

    # If no session factory provided, just return empty (useful for tests)
    if session_factory is None:
        return []

    # Database fallback (startup only, before cache is populated)
    async with session_factory() as session:
        result = await session.execute(select(Role).where(Role.status == RoleStatus.ACTIVE))
        snapshots = result.scalars().all()

        return [snap.role_definition for snap in snapshots if snap.role_definition]
