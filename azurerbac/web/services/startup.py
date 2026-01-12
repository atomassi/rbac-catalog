"""Application startup services."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from collections.abc import Callable

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from azurerbac.cache import get_cache_service
from azurerbac.cache.backends import CACHE_FILENAME
from azurerbac.core import Role
from azurerbac.core.constants import RoleStatus
from azurerbac.settings import Settings

logger = logging.getLogger(__name__)


async def preload_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Preload cache with commonly accessed data."""
    from azurerbac.telemetry import TimedDbQuery

    logger.info("CACHE INITIALIZATION STARTED")
    start = time.time()

    service = get_cache_service()

    async with session_factory() as session:
        logger.info("[Step 1/2] Rebuilding cache from database...")
        if not await service.rebuild_in_memory(session):
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
                    service.container.set_role_page(cache_key, list(page_roles))
                    pages_loaded += 1
            timer.rows = pages_loaded * 50
        logger.info("Loaded %d pages (%d roles)", pages_loaded, pages_loaded * 50)

    elapsed = time.time() - start
    container = service.container
    roles_count = len(container.cache.roles_by_id)
    operations_count = len(container.cache.all_operations)
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

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.telemetry import track_cache_refresh, track_cache_refresh_failure

    settings = Settings.get()
    service = get_cache_service()
    # Track when we last rebuilt from database
    last_db_rebuild = time.time()

    # Subscribe to cache change notifications (file watcher for FileCacheBackend)
    if service.backend.subscribe(service.mark_pending_reload):
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
                reloaded = await service.reload_if_needed()
                if reloaded:
                    elapsed = time.time() - start_time
                    logger.info("Background: cache reloaded from disk in %.2fs", elapsed)
                    last_db_rebuild = time.time()  # Reset timer on any refresh

                    # Keep AI recommender consistent with the in-memory cache
                    try:
                        from azurerbac.airecommender import get_ai_recommender

                        active_roles = service.container.get_all_roles()
                        get_ai_recommender().initialize(active_roles)
                    except Exception as e:
                        logger.warning(
                            "Background: AI recommender reload failed (non-fatal): %s", e
                        )

                    container = service.container
                    track_cache_refresh(
                        elapsed,
                        "worker",
                        len(container.cache.roles_by_id),
                        len(container.get_all_operations()),
                    )
                    continue

                # Periodic rebuild from database every 1 hour
                if time.time() - last_db_rebuild >= settings.db_rebuild_interval_seconds:
                    logger.info("Background: periodic rebuild from database (1 hour elapsed)")
                    start_time = time.time()
                    async with session_factory() as session:
                        if await service.rebuild_in_memory(session):
                            elapsed = time.time() - start_time
                            last_db_rebuild = time.time()
                            logger.info("Background: periodic rebuild completed in %.2fs", elapsed)
                            container = service.container
                            track_cache_refresh(
                                elapsed,
                                "periodic",
                                len(container.cache.roles_by_id),
                                len(container.get_all_operations()),
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
        service.backend.unsubscribe()
        raise  # Always re-raise CancelledError


async def ensure_db(engine: AsyncEngine) -> None:
    """Ensure database tables exist."""
    from azurerbac.core import ensure_db as ensure_db_core

    await ensure_db_core(engine)


async def _run_in_thread(func: Callable[[], None]) -> None:
    """Run blocking function in thread pool."""
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, func)


async def warmup_colbert() -> None:
    """Pre-warm ColBERT engine."""

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

    await _run_in_thread(_warmup)


async def warmup_crossencoder() -> None:
    """Pre-warm CrossEncoder model."""

    def _warmup() -> None:
        try:
            logger.info("CROSSENCODER WARMUP: Starting...")
            start = time.time()
            from azurerbac.airecommender.engines.crossencoder import get_cross_encoder

            get_cross_encoder()
            logger.info("CROSSENCODER WARMUP: Initialized in %.2fs", time.time() - start)
        except Exception as e:
            logger.exception("CROSSENCODER WARMUP: Failed (non-fatal): %s", e)

    await _run_in_thread(_warmup)
