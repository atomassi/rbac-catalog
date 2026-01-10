"""Cache build and refresh operations.

This module provides a clean separation of concerns:
- build_cache_from_db: Pure function that queries DB and returns CacheData
- swap_cache_in_memory: Side effect - swaps data into app_cache
- save_cache_to_disk: Side effect - persists to backend
- Convenience wrappers combine these for common use cases
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import OperationData
from azurerbac.cache.backends import get_cache_backend
from azurerbac.cache.models import CacheData, CachedChangeEvent, CachedRole
from azurerbac.core.constants import RoleStatus

logger = logging.getLogger(__name__)

# Module-level lock to prevent concurrent cache rebuilds
_rebuild_lock = threading.Lock()


async def build_cache_from_db(session: AsyncSession) -> CacheData:
    """Build cache data from database (pure function).

    Queries database for all roles, operations, and events, builds indexes,
    and returns a complete CacheData object. Does NOT modify any global state.

    Args:
        session: SQLAlchemy async session for queries

    Returns:
        Complete CacheData ready for use

    Raises:
        Exception: If database queries fail
    """
    from sqlalchemy import func, select

    from azurerbac.cache import (
        CacheMetadata,
        app_cache,
        compute_operations_hash,
        compute_roles_hash,
    )
    from azurerbac.cache.precompute import precompute_all_caches
    from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus
    from azurerbac.telemetry import TimedDbQuery

    logger.info("Building cache from database...")

    # Fetch ALL roles (active and deleted) for roles_by_id index
    async with TimedDbQuery("fetch_all_roles") as timer:
        all_roles_result = await session.execute(select(Role))
        all_role_snapshots = list(all_roles_result.scalars().all())
        timer.rows = len(all_role_snapshots)

    # Fetch active roles for role_jsons (used by recommender)
    active_roles = [r for r in all_role_snapshots if r.status == RoleStatus.ACTIVE]
    logger.debug(f"Found {len(active_roles)} active roles out of {len(all_role_snapshots)} total")

    # Fetch all operations
    async with TimedDbQuery("fetch_all_operations") as timer:
        ops_result = await session.execute(select(Operation))
        all_ops = list(ops_result.scalars().all())
        timer.rows = len(all_ops)

    # Fetch all history events
    async with TimedDbQuery("fetch_all_history_events") as timer:
        events_result = await session.execute(
            select(RoleHistory).order_by(RoleHistory.scan_id.desc())
        )
        all_events = list(events_result.scalars().all())
        timer.rows = len(all_events)

    # Get scan status
    async with TimedDbQuery("fetch_scan_status"):
        last_scan = await session.scalar(
            select(RoleScanStatus.scan_timestamp)
            .order_by(RoleScanStatus.scan_timestamp.desc())
            .limit(1)
        )
        first_scan = await session.scalar(select(func.min(RoleScanStatus.scan_timestamp)))

    # Collect RoleDefinition objects
    role_definitions = [role.role_definition for role in active_roles if role.role_definition]

    # Convert DB Operation models to OperationData Pydantic models
    all_operations = [
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
        for op in all_ops
    ]

    # Build roles_by_id index with CachedRole objects
    roles_by_id: dict[str, CachedRole] = {}
    for role in all_role_snapshots:
        role_def = role.last_known_definition
        if role_def:
            roles_by_id[role.role_id] = CachedRole(
                definition=role_def,
                status=role.status,
                last_seen_at=role.last_seen_at,
            )

    # Build change events list
    all_change_events: list[CachedChangeEvent] = []
    for ev in all_events:
        cached_role = roles_by_id.get(ev.role_id)
        role_name = cached_role.role_name if cached_role else ev.role_name
        all_change_events.append(
            CachedChangeEvent(
                id=ev.id,
                role_id=ev.role_id,
                role_name=role_name,
                event_type=ev.event_type,
                scan_timestamp=ev.scan.scan_timestamp if ev.scan else None,
                azure_updated_on=ev.azure_updated_on,
                summary=ev.summary,
                diff_json=ev.diff_json,
                role_json=ev.role_definition.to_dict() if ev.role_definition else None,
            )
        )

    # Compute hashes
    roles_hash = compute_roles_hash(role_definitions)
    operations_hash = compute_operations_hash(all_operations)

    # Create metadata
    metadata = CacheMetadata(
        roles_count=len(active_roles),
        operations_count=len(all_operations),
        roles_hash=roles_hash,
        operations_hash=operations_hash,
    )

    # Precompute all caches and build complete CacheData
    # swap_in_memory=False: don't modify global state
    cache_data = precompute_all_caches(
        role_definitions,
        all_operations,
        app_cache,
        metadata=metadata,
        roles_by_id=roles_by_id,
        all_change_events=all_change_events,
        last_scan=last_scan.replace(microsecond=0) if last_scan else None,
        first_scan=first_scan.replace(microsecond=0) if first_scan else None,
        swap_in_memory=False,
    )

    logger.info(
        f"Cache built: {len(active_roles)} roles, {len(all_operations)} operations, "
        f"{len(roles_by_id)} indexed, {len(all_change_events)} events"
    )

    return cache_data


def swap_cache_in_memory(cache_data: CacheData) -> None:
    """Swap cache data into the in-memory app_cache.

    Performs atomic swap of all cache data and updates tracking state.

    Args:
        cache_data: Complete cache data to swap in
    """
    from azurerbac.cache import app_cache
    from azurerbac.cache.precompute import precompute_all_caches

    # Extract active role definitions from roles_by_id
    active_roles = [
        cached_role.definition
        for cached_role in cache_data.roles_by_id.values()
        if cached_role.status == RoleStatus.ACTIVE
    ]

    # Use precompute_all_caches with swap_in_memory=True to do atomic swap
    precompute_all_caches(
        active_roles,
        cache_data.all_operations,
        app_cache,
        metadata=cache_data.metadata,
        roles_by_id=cache_data.roles_by_id,
        all_change_events=cache_data.all_change_events,
        last_scan=cache_data.last_scan,
        first_scan=cache_data.first_scan,
        swap_in_memory=True,
    )

    # Update tracking state
    backend = get_cache_backend()
    app_cache.loaded_cache_version = backend.get_version()
    app_cache.is_preloaded = True

    logger.debug("Cache swapped into memory")


async def save_cache_to_disk(cache_data: CacheData) -> None:
    """Save cache data to disk via the backend.

    Args:
        cache_data: Complete cache data to persist
    """
    await get_cache_backend().save(cache_data)
    logger.debug("Cache saved to disk")


def _initialize_ai_recommender(cache_data: CacheData) -> None:
    """Re-initialize AI recommender with updated role data."""
    try:
        from azurerbac.airecommender import get_ai_recommender

        ai_recommender = get_ai_recommender()
        # Extract active role definitions from roles_by_id
        active_roles = [
            cached_role.definition
            for cached_role in cache_data.roles_by_id.values()
            if cached_role.status == RoleStatus.ACTIVE
        ]
        ai_recommender.initialize(active_roles)
        logger.info("AI recommender re-initialized with updated roles")
    except Exception as e:
        logger.exception("Failed to re-initialize AI recommender: %s", e)


# ============================================================================
# Convenience wrappers for common use cases
# ============================================================================


async def rebuild_cache_in_memory(session: AsyncSession) -> bool:
    """Build cache from DB and swap into memory (web app startup).

    Thread-safe: Uses lock to prevent concurrent rebuilds.

    Args:
        session: SQLAlchemy async session

    Returns:
        True if successful, False if rebuild already in progress or failed
    """
    if not _rebuild_lock.acquire(blocking=False):
        logger.warning("Cache rebuild already in progress, skipping")
        return False

    try:
        cache_data = await build_cache_from_db(session)
        swap_cache_in_memory(cache_data)
        _initialize_ai_recommender(cache_data)
        return True
    except Exception as e:
        logger.exception("Failed to rebuild cache in memory: %s", e)
        return False
    finally:
        _rebuild_lock.release()


async def rebuild_cache_to_disk(session: AsyncSession) -> bool:
    """Build cache from DB and save to disk only (worker process).

    Thread-safe: Uses lock to prevent concurrent rebuilds.

    Args:
        session: SQLAlchemy async session

    Returns:
        True if successful, False if rebuild already in progress or failed
    """
    if not _rebuild_lock.acquire(blocking=False):
        logger.warning("Cache rebuild already in progress, skipping")
        return False

    try:
        cache_data = await build_cache_from_db(session)
        await save_cache_to_disk(cache_data)
        return True
    except Exception as e:
        logger.exception("Failed to rebuild cache to disk: %s", e)
        return False
    finally:
        _rebuild_lock.release()


async def invalidate_and_rebuild_cache(session: AsyncSession) -> bool:
    """Delete cache file and rebuild from DB (worker after changes).

    Called by worker process after detecting role/operation changes.
    Web processes will reload via mtime check.

    Args:
        session: SQLAlchemy async session

    Returns:
        True if successful, False otherwise
    """
    logger.info("Invalidating and rebuilding cache")
    await get_cache_backend().delete()
    return await rebuild_cache_to_disk(session)
