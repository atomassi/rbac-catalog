"""Cache operations and utilities."""

from __future__ import annotations

import logging
import threading

from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.cache.persistence import delete_cache_file
from azurerbac.core.constants import RoleStatus

logger = logging.getLogger(__name__)

# Module-level lock to prevent concurrent cache rebuilds
# This protects against:
# 1. Multiple periodic rebuilds from web app
# 2. Worker rebuild while web app is rebuilding
# 3. Multiple worker processes trying to rebuild simultaneously
_rebuild_lock = threading.Lock()


async def rebuild_cache(
    session: AsyncSession,
    logger_name: str = "azurerbac.cache",
    update_in_memory: bool = True,
) -> bool:
    """Rebuild the cache from the database, save to disk, and update in-memory cache.

    This function:
    1. Queries the database for all roles, operations, and events
    2. Builds all indexes (roles_by_id, ops_by_name_lower, ops_by_prefix)
    3. Saves to disk for other processes to load
    4. Updates the in-memory app_cache with atomic swap
    5. Precomputes all recommender caches (patterns, role coverage, etc.)

    Thread-safe: Uses a module-level lock to prevent concurrent rebuilds.

    Args:
        session: SQLAlchemy async session to use for queries
        logger_name: Logger name to use for log messages
        update_in_memory: If True, also update the in-memory app_cache.
            Set to False if running in worker process (no web server).

    Returns:
        True if cache was rebuilt successfully, False otherwise
    """
    from sqlalchemy import func, select

    from azurerbac.cache import (
        CacheMetadata,
        app_cache,
        compute_operations_hash,
        compute_roles_hash,
        save_cache_to_disk,
    )
    from azurerbac.cache.precompute import precompute_all_caches
    from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus
    from azurerbac.telemetry import TimedDbQuery

    log = logging.getLogger(logger_name)

    # Acquire lock to prevent concurrent rebuilds
    if not _rebuild_lock.acquire(blocking=False):
        log.warning("Cache rebuild already in progress, skipping")
        return False

    try:
        log.info("Rebuilding cache from database...")

        # Fetch ALL roles (active and deleted) for roles_by_id index
        async with TimedDbQuery("fetch_all_roles") as timer:
            all_roles_result = await session.execute(select(Role))
            all_role_snapshots = list(all_roles_result.scalars().all())
            timer.rows = len(all_role_snapshots)

        # Fetch active roles for role_jsons (used by recommender)
        active_roles = [r for r in all_role_snapshots if r.status == RoleStatus.ACTIVE]
        log.debug(f"Found {len(active_roles)} active roles out of {len(all_role_snapshots)} total")

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

        # Convert to serializable dicts
        role_jsons = [role.role_json for role in active_roles]
        all_operations = [
            {
                "name": op.name,
                "display_name": op.display_name,
                "description": op.description,
                "provider_display_name": op.provider_display_name,
                "resource_type_display_name": op.resource_type_display_name,
                "is_data_action": op.is_data_action,
            }
            for op in all_ops
        ]

        # Build roles_by_id index
        roles_by_id = {
            role.role_id: {
                "role_id": role.role_id,
                "role_name": role.role_name,
                "role_type": role.role_type,
                "status": role.status,
                "created_on": role.created_on,
                "updated_on": role.updated_on,
                "last_seen_at": role.last_seen_at,
                "role_json": role.role_json,
                "last_known_json": role.last_known_json,
            }
            for role in all_role_snapshots
        }

        # Build change events list (include role_name for display)
        all_change_events = []
        for ev in all_events:
            role_data = roles_by_id.get(ev.role_id, {})
            role_name = role_data.get("role_name", "") or ev.role_name
            all_change_events.append(
                {
                    "id": ev.id,
                    "role_id": ev.role_id,
                    "role_name": role_name,
                    "event_type": ev.event_type,
                    "scan_timestamp": ev.scan.scan_timestamp if ev.scan else None,
                    "azure_updated_on": ev.azure_updated_on,
                    "summary": ev.summary,
                    "diff_json": ev.diff_json,
                    "role_json": ev.role_json,
                }
            )

        # Compute hashes
        roles_hash = compute_roles_hash(role_jsons)
        operations_hash = compute_operations_hash(all_operations)

        # Create metadata
        metadata = CacheMetadata(
            roles_count=len(active_roles),
            operations_count=len(all_operations),
            roles_hash=roles_hash,
            operations_hash=operations_hash,
        )

        # Precompute all caches and build complete CacheData.
        # This includes source data + computed fields.
        # If update_in_memory=True, also swaps into memory atomically.
        cache_data = precompute_all_caches(
            role_jsons,
            all_operations,
            app_cache,
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_change_events=all_change_events,
            last_scan=last_scan.replace(microsecond=0) if last_scan else None,
            first_scan=first_scan.replace(microsecond=0) if first_scan else None,
            swap_in_memory=update_in_memory,
        )

        # Save complete cache to disk (includes all computed fields).
        # Other processes just load and swap - no recomputation needed.
        save_cache_to_disk(cache_data)

        # Update cache tracking state if we swapped in memory
        if update_in_memory:
            import time as time_module

            from azurerbac.cache.persistence import get_cache_file_mtime

            app_cache.loaded_cache_mtime = get_cache_file_mtime()
            app_cache.last_cache_check = time_module.time()
            app_cache.is_preloaded = True

            log.debug("Precomputed caches, swapped in-memory, and saved to disk")

        log.info(
            f"Cache rebuilt: {len(active_roles)} roles, {len(all_operations)} operations, "
            f"{len(roles_by_id)} roles indexed, {len(all_change_events)} events"
        )

        # Re-initialize AI recommender with updated role data
        try:
            from azurerbac.airecommender import get_ai_recommender

            ai_recommender = get_ai_recommender()
            ai_recommender.initialize(role_jsons)
            log.info("AI recommender re-initialized with updated roles")
        except Exception as e:
            log.exception("Failed to re-initialize AI recommender: %s", e)

        return True

    except Exception as e:
        log.exception("Failed to rebuild cache: %s", e)
        return False
    finally:
        _rebuild_lock.release()


async def invalidate_and_rebuild_cache(session: AsyncSession) -> bool:
    """Invalidate and rebuild cache from database.

    Called by worker process after detecting role/operation changes.
    Deletes cache file and rebuilds from DB. Web processes reload via mtime check.

    Args:
        session: SQLAlchemy async session to use for queries

    Returns:
        True if cache was rebuilt successfully, False otherwise
    """
    logger.info("Invalidating and rebuilding cache")
    delete_cache_file()
    return await rebuild_cache(session, update_in_memory=False)
