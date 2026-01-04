"""Cache data access services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import select

from azurerbac.cache import AppCache, app_cache
from azurerbac.core import Operation, Role
from azurerbac.core.constants import RoleStatus

if TYPE_CHECKING:
    from azurerbac.core.db import AsyncSessionLocal


@asynccontextmanager
async def _session_factory_or_default(
    session_factory: AsyncSessionLocal | None,
) -> AsyncIterator[AsyncSessionLocal]:
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
    session_factory: AsyncSessionLocal | None = None,
) -> list[dict]:
    """Get all operations from cache or database.

    Args:
        cache: Optional cache instance (defaults to app_cache singleton)
        session_factory: Optional session factory for database fallback.
            If not provided and cache is empty, returns empty list.

    Returns:
        List of operation dictionaries with name, display_name, description, etc.
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
            {
                "name": op.name,
                "display_name": op.display_name,
                "description": op.description,
                "provider_display_name": op.provider_display_name,
                "resource_type_display_name": op.resource_type_display_name,
                "is_data_action": op.is_data_action,
            }
            for op in operations
        ]
        cache.build_from_operations(ops_list)
        return ops_list


async def get_all_role_jsons(
    cache: AppCache | None = None,
    session_factory: AsyncSessionLocal | None = None,
) -> list[dict]:
    """Get all active role JSONs from cache or database.

    Args:
        cache: Optional cache instance (defaults to app_cache singleton)
        session_factory: Optional session factory for database fallback.
            If not provided and cache is empty, returns empty list.

    Returns:
        List of role JSON dictionaries from active role snapshots.
    """
    cache = cache or app_cache

    cached = cache.get_all_role_jsons()
    if cached:
        return cached

    # If no session factory provided, just return empty (useful for tests)
    if session_factory is None:
        return []

    async with session_factory() as session:
        # Role.current_version is eager-loaded (lazy=\"joined\")
        result = await session.execute(select(Role).where(Role.status == RoleStatus.ACTIVE))
        snapshots = result.scalars().all()

        # Build roles from DB and cache them
        roles_list = []
        for snap in snapshots:
            if snap.current_version:
                role_json = snap.current_version.role_json
                props = role_json.get("properties", {})
                roles_list.append(
                    {
                        "role_id": snap.role_id,
                        "role_name": props.get("roleName", snap.role_id),
                        "role_type": props.get("type"),
                        "status": snap.status,
                        "updated_on": snap.current_version.azure_updated_on,
                        "last_seen_at": snap.last_seen_at,
                        "role_json": role_json,
                    }
                )

        cache.build_from_roles(roles_list)
        return [role["role_json"] for role in roles_list]


async def get_operations_for_recommender(
    cache: AppCache | None = None,
    session_factory: AsyncSessionLocal | None = None,
) -> list[dict]:
    """Get operations in the format needed by the recommender.

    Args:
        cache: Optional cache instance (defaults to app_cache singleton)
        session_factory: Optional session factory for database fallback

    Returns:
        List of dicts with 'name' and 'is_data_action' keys only.
    """
    cache = cache or app_cache

    cached = cache.cache.operations_for_recommender
    if cached:
        return cached

    all_ops = await get_all_operations(cache, session_factory)
    ops_for_recommender = [
        {"name": op["name"], "is_data_action": op["is_data_action"]} for op in all_ops
    ]
    cache.set_metadata(operations_for_recommender=ops_for_recommender)
    return ops_for_recommender
