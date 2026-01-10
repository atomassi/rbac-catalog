"""Cache service - orchestrates container and backend.

The CacheService is the main entry point for all cache operations.
It owns both the in-memory CacheContainer and the CacheBackend (disk I/O).

Architecture:
    CacheService (orchestrator)
    ├── CacheContainer (in-memory data + accessors)
    └── CacheBackend (disk I/O - FileCacheBackend by default)

Usage:
    from azurerbac.cache import get_cache_service

    service = get_cache_service()

    # Access in-memory data
    role = service.container.get_role_by_id(role_id)
    ops = service.container.get_all_operations()

    # High-level operations
    await service.reload_if_needed()
    await service.rebuild_in_memory(session)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final

from azurerbac.cache.backends.base import CacheBackend
from azurerbac.cache.container import CacheContainer
from azurerbac.cache.models import CACHE_VERSION, CacheData
from azurerbac.core.constants import RoleStatus
from azurerbac.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Lock to prevent concurrent cache rebuilds
_REBUILD_LOCK: Final[threading.Lock] = threading.Lock()


class CacheService:
    """Orchestrates cache container and backend.

    Provides high-level cache operations:
    - Build cache from database
    - Save/load cache to/from backend
    - Reload cache when backend updates (worker → web app)
    - Invalidate and rebuild

    The service owns both the container (in-memory) and backend (disk I/O).
    Access the container for read operations, use service methods for writes.
    """

    __slots__ = ("_backend", "_container")

    def __init__(
        self,
        container: CacheContainer | None = None,
        backend: CacheBackend | None = None,
    ) -> None:
        """Initialize the cache service.

        Args:
            container: In-memory cache container. Creates new instance if not provided.
            backend: Storage backend. Creates based on settings if not provided.
        """
        from azurerbac.cache.backends.base import create_backend

        self._container = container or CacheContainer()
        self._backend = backend or create_backend()

    # ─────────────────────────────────────────────────────────────────────────
    # Properties
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def container(self) -> CacheContainer:
        """The in-memory cache container."""
        return self._container

    @property
    def backend(self) -> CacheBackend:
        """The storage backend."""
        return self._backend

    # ─────────────────────────────────────────────────────────────────────────
    # Build from database
    # ─────────────────────────────────────────────────────────────────────────

    async def build_from_db(self, session: AsyncSession) -> CacheData:
        """Build complete cache data from database.

        Fetches all data from DB and precomputes derived data.
        Does NOT swap into memory or save to backend - caller decides.

        Args:
            session: SQLAlchemy async session

        Returns:
            Complete CacheData ready for use
        """
        # Import here to avoid circular dependencies
        from azurerbac.cache.build import build_from_db

        return await build_from_db(session)

    # ─────────────────────────────────────────────────────────────────────────
    # Swap and save
    # ─────────────────────────────────────────────────────────────────────────

    def swap_in_memory(self, cache_data: CacheData) -> None:
        """Swap cache data into the in-memory container."""
        self._container.swap(cache_data)
        self._container.loaded_version = self._backend.get_version()
        self._container.is_preloaded = True
        logger.debug("Cache swapped into memory")

    async def save_to_backend(self, cache_data: CacheData) -> bool:
        """Save cache data to backend storage."""
        success = await self._backend.save(cache_data)
        if success:
            logger.debug("Cache saved to backend")
        return success

    # ─────────────────────────────────────────────────────────────────────────
    # High-level rebuild operations
    # ─────────────────────────────────────────────────────────────────────────

    async def rebuild_in_memory(self, session: AsyncSession) -> bool:
        """Build cache from DB and swap into memory (web app startup).

        Thread-safe: Uses lock to prevent concurrent rebuilds.

        Args:
            session: SQLAlchemy async session

        Returns:
            True if successful, False if rebuild in progress or failed
        """
        if not _REBUILD_LOCK.acquire(blocking=False):
            logger.warning("Cache rebuild already in progress, skipping")
            return False

        try:
            cache_data = await self.build_from_db(session)
            self.swap_in_memory(cache_data)
            self._initialize_ai_recommender(cache_data)
            return True
        except Exception as e:
            logger.exception("Failed to rebuild cache in memory: %s", e)
            return False
        finally:
            _REBUILD_LOCK.release()

    async def rebuild_and_save(self, session: AsyncSession) -> bool:
        """Build cache from DB and save to backend (worker process).

        Args:
            session: SQLAlchemy async session

        Returns:
            True if successful, False otherwise
        """
        if not _REBUILD_LOCK.acquire(blocking=False):
            logger.warning("Cache rebuild already in progress, skipping")
            return False

        try:
            cache_data = await self.build_from_db(session)
            await self.save_to_backend(cache_data)
            return True
        except Exception as e:
            logger.exception("Failed to rebuild cache to backend: %s", e)
            return False
        finally:
            _REBUILD_LOCK.release()

    async def invalidate_and_rebuild(self, session: AsyncSession) -> bool:
        """Delete cache and rebuild from DB (worker after changes).

        Args:
            session: SQLAlchemy async session

        Returns:
            True if successful, False otherwise
        """
        logger.info("Invalidating and rebuilding cache")
        await self._backend.delete()
        return await self.rebuild_and_save(session)

    async def invalidate_all(self) -> None:
        """Reset in-memory cache and delete backend storage."""
        self._container.reset()
        await self._backend.delete()

    # ─────────────────────────────────────────────────────────────────────────
    # Reload from backend (web app detecting worker update)
    # ─────────────────────────────────────────────────────────────────────────

    def needs_reload(self) -> bool:
        """Check if backend cache was updated by worker.

        Compares loaded version with current backend version.

        Returns:
            True if cache version changed since last load.
        """
        current_version = self._backend.get_version()

        if current_version is None:
            return False

        if self._container.loaded_version is None:
            logger.info("New cache detected from worker (version: %s)", current_version)
            return True

        if current_version != self._container.loaded_version:
            logger.info(
                "Cache updated (version: %s != %s)",
                current_version,
                self._container.loaded_version,
            )
            return True

        return False

    def mark_pending_reload(self) -> None:
        """Mark that a reload is pending (called by backend watcher).

        Called from the watchdog thread when a cache change is detected.
        The actual reload happens on the next async check.
        """
        self._container.pending_reload = True
        logger.debug("Cache reload marked as pending (watcher triggered)")

    async def reload_if_needed(self) -> bool:
        """Reload cache from backend if updated by worker.

        Uses async lock to prevent concurrent reloads.
        The backend file contains complete CacheData - no recomputation needed.

        Returns:
            True if cache was reloaded, False otherwise.
        """
        # Quick check without lock
        if not self._container.pending_reload and not self.needs_reload():
            return False

        reload_lock = self._container.reload_lock
        if reload_lock.locked():
            logger.debug("Cache reload already in progress, skipping")
            return False

        async with reload_lock:
            self._container.pending_reload = False

            if not self.needs_reload():
                return False

            logger.info("Reloading cache from backend...")
            start_time = time.time()

            cached = await self._backend.load()
            if cached is None:
                logger.warning("Failed to load cache from backend")
                return False

            current_version = self._backend.get_version()

            # Validate required data
            if not cached.roles_by_id or not cached.all_operations:
                logger.warning("Loaded cache incomplete, keeping current")
                self._container.loaded_version = current_version
                return False

            # Check version compatibility
            if cached.metadata.version != CACHE_VERSION:
                logger.warning(
                    f"Cache version mismatch: {cached.metadata.version} != {CACHE_VERSION}, "
                    "keeping current (worker will rebuild)"
                )
                self._container.loaded_version = current_version
                return False

            # Swap in the loaded cache
            self._container.swap(cached)
            self._container.loaded_version = current_version
            self._container.is_preloaded = True

            elapsed = time.time() - start_time
            logger.info(
                f"Cache reloaded in {elapsed:.2f}s: {len(cached.roles_by_id)} roles, "
                f"{len(cached.all_operations)} ops, "
                f"{len(cached.role_coverage)} role coverages (precomputed)"
            )
            return True

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _initialize_ai_recommender(self, cache_data: CacheData) -> None:
        """Re-initialize AI recommender with updated role data."""
        try:
            from azurerbac.airecommender import get_ai_recommender

            ai_recommender = get_ai_recommender()
            active_roles = [
                cached_role.definition
                for cached_role in cache_data.roles_by_id.values()
                if cached_role.status == RoleStatus.ACTIVE
            ]
            ai_recommender.initialize(active_roles)
            logger.info("AI recommender re-initialized with updated roles")
        except Exception as e:
            logger.exception("Failed to re-initialize AI recommender: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton access
# ─────────────────────────────────────────────────────────────────────────────

_service_singleton: ThreadSafeSingleton[CacheService] = ThreadSafeSingleton(CacheService)


def get_cache_service() -> CacheService:
    """Get the global cache service instance (thread-safe singleton)."""
    return _service_singleton.get()
