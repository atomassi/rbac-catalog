"""Application startup services."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import anyio
from anyio import to_thread
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from azurerbac.cache import get_cache_service
from azurerbac.settings import Settings

logger = logging.getLogger(__name__)


async def preload_cache(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Preload cache with commonly accessed data."""
    logger.info("CACHE INITIALIZATION STARTED")
    start = time.time()

    service = get_cache_service()

    async with session_factory() as session:
        logger.info("Rebuilding cache from database...")
        if not await service.rebuild_in_memory(session):
            raise RuntimeError("Cache initialization failed - cannot start without cache")

    elapsed = time.time() - start
    roles_count = service.cache.metadata.roles_count
    operations_count = service.cache.metadata.operations_count
    logger.info("CACHE INITIALIZATION COMPLETE in %.2fs", elapsed)
    logger.info("Roles: %d | Operations: %d", roles_count, operations_count)

    # Track startup metrics to Application Insights
    from azurerbac.telemetry import track_cache_refresh, track_startup

    track_startup(elapsed, roles_count, operations_count)
    track_cache_refresh(elapsed, "startup", roles_count, operations_count)


async def cache_refresh_task(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background task for periodic cache refresh from database.

    Simple refresh flow:
    - At startup: preload_cache() builds from database
    - Every 2 hours: rebuild from database to ensure consistency

    Args:
        session_factory: Async session factory for database access
    """
    from azurerbac.telemetry import track_cache_refresh, track_cache_refresh_failure

    settings = Settings.get()
    service = get_cache_service()

    try:
        while True:
            # Sleep for the configured interval (default: 2 hours)
            await anyio.sleep(settings.db_rebuild_interval_seconds)

            try:
                logger.info("Background: periodic rebuild from database")
                start_time = time.time()

                async with session_factory() as session:
                    if await service.rebuild_in_memory(session):
                        elapsed = time.time() - start_time
                        logger.info("Background: periodic rebuild completed in %.2fs", elapsed)

                        track_cache_refresh(
                            elapsed,
                            "periodic",
                            service.cache.metadata.roles_count,
                            service.cache.metadata.operations_count,
                        )
                    else:
                        logger.warning("Background: periodic rebuild skipped or failed")
                        track_cache_refresh_failure("periodic", "rebuild_in_memory returned False")

            except anyio.get_cancelled_exc_class():
                raise  # Re-raise to allow clean shutdown
            except Exception as e:
                logger.exception("Background: cache refresh error: %s", e)
                track_cache_refresh_failure("background_task", str(e))

    except anyio.get_cancelled_exc_class():
        logger.info("Background: shutting down cache refresh task")
        raise  # Always re-raise CancelledError


async def ensure_db(engine: AsyncEngine) -> None:
    """Ensure database tables exist."""
    from azurerbac.core import ensure_db as ensure_db_core

    await ensure_db_core(engine)


async def _run_in_thread(func: Callable[[], None]) -> None:
    """Run blocking function in thread pool."""
    await to_thread.run_sync(func)


async def _warmup_engine(name: str, factory: Callable[[], object]) -> None:
    """Pre-warm an AI engine in a background thread."""

    def _warmup() -> None:
        try:
            logger.info("%s WARMUP: Starting...", name)
            start = time.time()
            result = factory()
            # ColBERT returns bool from warmup(); others just need to be called
            if result is False:
                logger.warning("%s WARMUP: Skipped (no pre-built index)", name)
            else:
                logger.info("%s WARMUP: Initialized in %.2fs", name, time.time() - start)
        except Exception as e:
            logger.exception("%s WARMUP: Failed (non-fatal): %s", name, e)

    await _run_in_thread(_warmup)


async def warmup_colbert() -> None:
    """Pre-warm ColBERT engine."""
    from azurerbac.airecommender.engines.colbert import get_colbert_index

    await _warmup_engine("COLBERT", lambda: get_colbert_index().warmup())


async def warmup_crossencoder() -> None:
    """Pre-warm CrossEncoder model."""
    from azurerbac.airecommender.engines.crossencoder import get_cross_encoder

    await _warmup_engine("CROSSENCODER", get_cross_encoder)
