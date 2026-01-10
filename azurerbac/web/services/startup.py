"""Application startup and cache lifecycle services."""

from __future__ import annotations

import asyncio
import logging
import time

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from azurerbac.cache import get_cache_container
from azurerbac.cache.backends import CACHE_FILENAME, get_cache_backend
from azurerbac.core import Role
from azurerbac.core.constants import RoleStatus
from azurerbac.settings import Settings

logger = logging.getLogger(__name__)


async def preload_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Preload cache with commonly accessed data at startup.

    Uses the shared cache rebuild logic to populate the unified CacheData
    (raw + indexes + computed fields), then warms common role-list pages.

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.cache.build import rebuild_in_memory
    from azurerbac.telemetry import TimedDbQuery

    logger.info("CACHE INITIALIZATION STARTED")
    start = time.time()

    async with session_factory() as session:
        logger.info("[Step 1/2] Rebuilding cache from database...")
        if not await rebuild_in_memory(session):
            raise RuntimeError("Cache initialization failed - cannot start without cache")

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
                    get_cache_container().set_role_page(cache_key, list(page_roles))
                    pages_loaded += 1
            timer.rows = pages_loaded * 50
        logger.info("Loaded %d pages (%d roles)", pages_loaded, pages_loaded * 50)

    elapsed = time.time() - start
    cache = get_cache_container()
    roles_count = len(cache.cache.roles_by_id)
    operations_count = len(cache.cache.all_operations)
    logger.info("CACHE INITIALIZATION COMPLETE in %.2fs", elapsed)
    logger.info("Roles: %d | Operations: %d", roles_count, operations_count)

    # Track startup metrics to Application Insights
    from azurerbac.telemetry import track_cache_refresh, track_startup

    track_startup(elapsed, roles_count, operations_count)
    track_cache_refresh(elapsed, "startup", roles_count, operations_count)


async def cache_refresh_task(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background task to check for cache updates and periodic recompute.

    The cache refresh flow:
    1. At startup: preload_cache() builds from database
    2. Worker detects changes: saves new cache to disk
    3. File watcher detects change, sets pending_reload flag
    4. This task checks the flag and reloads from disk
    5. Every 1 hour: rebuilds from database to ensure consistency

    Thread-safety:
    - reload_if_needed() uses async lock and thread pool for I/O
    - rebuild_in_memory() uses a lock to prevent concurrent rebuilds
    - Both use atomic swap to update in-memory cache safely while serving requests

    Cancellation:
    - Properly handles CancelledError for clean shutdown
    - Never swallows CancelledError without re-raising

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.cache.build import mark_pending_reload, rebuild_in_memory, reload_if_needed
    from azurerbac.telemetry import track_cache_refresh, track_cache_refresh_failure

    settings = Settings.get()
    # Track when we last rebuilt from database
    last_db_rebuild = time.time()

    # Subscribe to cache change notifications (file watcher for FileCacheBackend)
    backend = get_cache_backend()
    if backend.subscribe(mark_pending_reload):
        logger.info("Background: cache change subscription active for %s", CACHE_FILENAME)
    else:
        logger.warning(
            "Background: cache subscription not supported, will rely on polling fallback"
        )

    try:
        while True:
            await anyio.sleep(settings.cache_check_interval_seconds)
            try:
                # Check if worker has updated the disk cache (flag set by watcher)
                # reload_if_needed uses async lock and thread pool for I/O
                start_time = time.time()
                reloaded = await reload_if_needed()
                if reloaded:
                    elapsed = time.time() - start_time
                    logger.info("Background: cache reloaded from disk in %.2fs", elapsed)
                    last_db_rebuild = time.time()  # Reset timer on any refresh

                    # Keep AI recommender consistent with the in-memory cache
                    try:
                        from azurerbac.airecommender import get_ai_recommender

                        active_roles = get_cache_container().get_all_roles()
                        get_ai_recommender().initialize(active_roles)
                    except Exception as e:
                        logger.warning(
                            "Background: AI recommender reload failed (non-fatal): %s", e
                        )

                    cache = get_cache_container()
                    track_cache_refresh(
                        elapsed,
                        "worker",
                        len(cache.cache.roles_by_id),
                        len(cache.get_all_operations()),
                    )
                    continue

                # Periodic rebuild from database every 1 hour
                if time.time() - last_db_rebuild >= settings.db_rebuild_interval_seconds:
                    logger.info("Background: periodic rebuild from database (1 hour elapsed)")
                    start_time = time.time()
                    async with session_factory() as session:
                        if await rebuild_in_memory(session):
                            elapsed = time.time() - start_time
                            last_db_rebuild = time.time()
                            logger.info("Background: periodic rebuild completed in %.2fs", elapsed)
                            cache = get_cache_container()
                            track_cache_refresh(
                                elapsed,
                                "periodic",
                                len(cache.cache.roles_by_id),
                                len(cache.get_all_operations()),
                            )
                        else:
                            logger.warning("Background: periodic rebuild skipped or failed")
                            track_cache_refresh_failure(
                                "periodic", "rebuild_in_memory returned False"
                            )
            except anyio.get_cancelled_exc_class():
                raise  # Re-raise to allow clean shutdown
            except Exception as e:
                logger.exception("Background: cache refresh error: %s", e)
                track_cache_refresh_failure("background_task", str(e))
    except anyio.get_cancelled_exc_class():
        logger.info("Background: shutting down cache refresh task")
        get_cache_backend().unsubscribe()
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
