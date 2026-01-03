"""Application startup and cache lifecycle services."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import select

from azurerbac.cache import app_cache
from azurerbac.core import Role
from azurerbac.core.constants import RoleStatus
from azurerbac.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from azurerbac.core.db import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def preload_cache(session_factory: AsyncSessionLocal) -> None:
    """Preload cache with commonly accessed data at startup.

    Uses the shared cache rebuild logic to populate the unified CacheData
    (raw + indexes + computed fields), then warms common role-list pages.

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.cache.refresh import rebuild_cache
    from azurerbac.telemetry import TimedDbQuery

    logger.info("CACHE INITIALIZATION STARTED")
    start = time.time()

    async with session_factory() as session:
        logger.info("[Step 1/2] Rebuilding cache from database...")
        if not await rebuild_cache(session, logger_name=__name__, update_in_memory=True):
            logger.warning("Cache rebuild failed or skipped; leaving cache uninitialized")
            return

        # Warm common role list pages (first 5 pages)
        logger.info("[Step 2/2] Preloading role pages (first 5 pages)...")
        async with TimedDbQuery("preload_role_pages") as timer:
            pages_loaded = 0
            for page in range(1, 6):
                offset = (page - 1) * 50
                roles_result = await session.execute(
                    select(Role)
                    .where(Role.status == RoleStatus.ACTIVE)
                    .order_by(Role.role_name.asc())
                    .offset(offset)
                    .limit(50)
                )
                page_roles = roles_result.scalars().all()
                if page_roles:
                    cache_key = f"roles:active::name:asc:{page}:50"
                    app_cache.set_role_page(cache_key, list(page_roles))
                    pages_loaded += 1
            timer.rows = pages_loaded * 50
        logger.info("Loaded %d pages (%d roles)", pages_loaded, pages_loaded * 50)

    elapsed = time.time() - start
    roles_count = len(app_cache.cache.roles_by_id)
    operations_count = len(app_cache.cache.all_operations)
    logger.info("CACHE INITIALIZATION COMPLETE in %.2fs", elapsed)
    logger.info("Roles: %d | Operations: %d", roles_count, operations_count)

    # Track startup metrics to Application Insights
    from azurerbac.telemetry import track_cache_refresh, track_startup

    track_startup(elapsed, roles_count, operations_count)
    track_cache_refresh(elapsed, "startup", roles_count, operations_count)


async def cache_refresh_task(session_factory: AsyncSessionLocal) -> None:
    """Background task to check for cache updates and periodic recompute.

    The cache refresh flow:
    1. At startup: preload_cache() builds from database
    2. Worker detects changes: saves new cache to disk
    3. This task checks disk mtime every 30s, reloads if worker updated
    4. Every 1 hour: rebuilds from database to ensure consistency

    Thread-safety:
    - reload_from_disk_if_needed() uses a lock to prevent concurrent reloads
    - rebuild_cache() uses a lock to prevent concurrent rebuilds
    - Both use atomic swap to update in-memory cache safely while serving requests

    Cancellation:
    - Properly handles CancelledError for clean shutdown
    - Never swallows CancelledError without re-raising

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.cache.refresh import rebuild_cache
    from azurerbac.telemetry import track_cache_refresh, track_cache_refresh_failure

    settings = Settings.get()
    # Track when we last rebuilt from database
    last_db_rebuild = time.time()

    try:
        while True:
            await anyio.sleep(settings.cache_check_interval_seconds)
            try:
                # Check if worker has updated the disk cache
                # reload_from_disk_if_needed is thread-safe and does atomic swap
                start_time = time.time()
                reloaded = app_cache.reload_from_disk_if_needed()
                if reloaded:
                    logger.info("Cache reloaded from disk (worker update detected)")
                    elapsed = time.time() - start_time
                    last_db_rebuild = time.time()  # Reset timer on any refresh

                    # Keep AI recommender consistent with the in-memory cache.
                    # The worker updates the disk cache; the web process swaps it in.
                    try:
                        from azurerbac.airecommender import get_ai_recommender

                        active_role_jsons = [
                            r.get("role_json")
                            for r in app_cache.cache.roles_by_id.values()
                            if r.get("status") == RoleStatus.ACTIVE and r.get("role_json")
                        ]
                        get_ai_recommender().initialize(active_role_jsons)
                    except Exception as e:
                        logger.warning("AI recommender reload failed (non-fatal): %s", e)

                    track_cache_refresh(
                        elapsed,
                        "worker",
                        len(app_cache.cache.roles_by_id),
                        len(app_cache.get_all_operations()),
                    )
                    continue

                # Periodic rebuild from database every 1 hour
                # rebuild_cache is thread-safe (has lock) and updates in-memory cache
                if time.time() - last_db_rebuild >= settings.db_rebuild_interval_seconds:
                    logger.info(
                        "Periodic cache rebuild from database (1 hour since last update)..."
                    )
                    start_time = time.time()
                    async with session_factory() as session:
                        # update_in_memory=True ensures app_cache is updated atomically
                        if await rebuild_cache(session, update_in_memory=True):
                            elapsed = time.time() - start_time
                            last_db_rebuild = time.time()
                            logger.info("Periodic cache rebuild completed in %.2fs", elapsed)
                            track_cache_refresh(
                                elapsed,
                                "periodic",
                                len(app_cache.cache.roles_by_id),
                                len(app_cache.get_all_operations()),
                            )
                        else:
                            # Lock was held by another rebuild or error occurred
                            logger.warning("Periodic cache rebuild skipped or failed")
                            track_cache_refresh_failure("periodic", "rebuild_cache returned False")
            except anyio.get_cancelled_exc_class():
                raise  # Re-raise to allow clean shutdown
            except Exception as e:
                logger.exception("Cache refresh task error: %s", e)
                track_cache_refresh_failure("background_task", str(e))
    except anyio.get_cancelled_exc_class():
        logger.info("Cache refresh task cancelled, shutting down...")
        raise  # Always re-raise CancelledError


async def ensure_db(engine: AsyncEngine) -> None:
    """Ensure database tables exist.

    Checks for one table (role_snapshots) since create_all creates all tables together.
    If one exists, all exist. If not, create_all creates all at once.

    Args:
        engine: Async database engine
    """
    from azurerbac.core import ensure_db as ensure_db_core

    await ensure_db_core(engine)


async def warmup_colbert() -> None:
    """Pre-warm ColBERT engine at startup to avoid slow first request.

    Runs in a thread pool to not block the event loop.
    """
    import concurrent.futures

    def _warmup() -> None:
        try:
            logger.info("COLBERT WARMUP: Starting...")
            start = time.time()

            from azurerbac.airecommender.engines.colbert import get_colbert_index

            if get_colbert_index().warmup():
                logger.info("COLBERT WARMUP: Initialized in %.2fs", time.time() - start)
            else:
                logger.warning("COLBERT WARMUP: Skipped (no pre-built index)")
        except Exception as e:
            logger.exception("COLBERT WARMUP: Failed (non-fatal): %s", e)

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, _warmup)
