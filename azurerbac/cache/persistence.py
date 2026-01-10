"""Cache persistence and synchronization.

This module handles:
- Saving/loading cache data to/from the configured backend
- Detecting when the backend was updated (e.g., by worker process)
- Reloading cache into app_cache when backend changes

For low-level backend operations, use get_cache_backend() directly.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

# Re-export backend for convenience
from azurerbac.cache.backends import (
    CACHE_FILENAME,
    CacheBackend,
    FileCacheBackend,
    get_cache_backend,
    set_cache_backend_factory,
)

if TYPE_CHECKING:
    from azurerbac.cache.models import CacheData

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "CACHE_FILENAME",
    "CacheBackend",
    "FileCacheBackend",
    "delete_cache_file",
    "get_cache_backend",
    "get_cache_dir",
    "get_cache_file_mtime",
    "get_cache_file_path",
    "load_cache_from_disk",
    "mark_pending_reload",
    "needs_reload_from_backend",
    "reload_from_backend_if_needed",
    "save_cache_to_disk",
    "set_cache_backend_factory",
]


def get_cache_dir() -> Path:
    """Get the cache directory path.

    Delegates to the configured cache backend.
    """
    backend = get_cache_backend()
    if isinstance(backend, FileCacheBackend):
        return backend.cache_dir
    # Fallback for non-file backends
    from azurerbac.settings import Settings, is_running_in_azure

    settings = Settings.get()
    if settings.cache_dir:
        return Path(settings.cache_dir)
    if is_running_in_azure():
        return Path("/home/cache")
    return Path(__file__).parent.parent / ".cache"


def get_cache_file_path() -> Path:
    """Get the path to the cache file.

    Only meaningful for FileCacheBackend.
    """
    backend = get_cache_backend()
    if isinstance(backend, FileCacheBackend):
        return backend.cache_file
    return get_cache_dir() / CACHE_FILENAME


def get_cache_file_mtime() -> float | None:
    """Get the modification time of the cache.

    Only available for FileCacheBackend. Returns None for other backends.
    """
    backend = get_cache_backend()
    if isinstance(backend, FileCacheBackend):
        return backend.get_mtime()
    # For non-file backends, try to parse version as float (if it's a timestamp)
    version = backend.get_version()
    if version:
        try:
            return float(version)
        except ValueError:
            return None
    return None


async def load_cache_from_disk() -> CacheData | None:
    """Load cache from storage asynchronously.

    Delegates to the configured cache backend.
    """
    return await get_cache_backend().load()


async def save_cache_to_disk(data: CacheData) -> bool:
    """Save cache to storage asynchronously.

    Delegates to the configured cache backend.
    """
    return await get_cache_backend().save(data)


async def delete_cache_file() -> None:
    """Delete the cached data.

    Delegates to the configured cache backend.
    """
    await get_cache_backend().delete()


# ============================================================================
# Backend sync functions (for web app detecting worker updates)
# ============================================================================


def needs_reload_from_backend() -> bool:
    """Check if backend cache was updated by another process.

    Compares loaded version with current backend version.

    Returns:
        True if cache version changed since last load.
    """
    from azurerbac.cache import app_cache

    current_version = get_cache_backend().get_version()

    # No cache in backend
    if current_version is None:
        return False

    # Cache exists but we haven't loaded any yet (worker created it)
    if app_cache.loaded_cache_version is None:
        logger.info("New cache detected from worker (version: %s)", current_version)
        return True

    # Cache version changed since we last loaded
    if current_version != app_cache.loaded_cache_version:
        logger.info(
            "Cache updated (version: %s != %s)",
            current_version,
            app_cache.loaded_cache_version,
        )
        return True
    return False


def mark_pending_reload() -> None:
    """Mark that a reload is pending (called by backend watcher).

    This is called from the watchdog thread when a cache change
    is detected. The actual reload happens on the next async check.
    """
    from azurerbac.cache import app_cache

    app_cache.pending_reload = True
    logger.debug("Cache reload marked as pending (watcher triggered)")


async def reload_from_backend_if_needed() -> bool:
    """Reload cache from backend if updated by another process.

    Uses AppCache's async lock to prevent concurrent reloads.
    The backend contains the complete CacheData with all computed fields.
    No recomputation is needed - just load and swap.

    Returns:
        True if cache was reloaded, False otherwise.
    """
    from azurerbac.cache import app_cache
    from azurerbac.cache.models import CACHE_VERSION

    # Quick check without lock - prefer pending_reload flag from watcher
    if not app_cache.pending_reload and not needs_reload_from_backend():
        return False

    # Use AppCache's lock to prevent concurrent reloads
    reload_lock = app_cache.reload_lock
    if reload_lock.locked():
        logger.debug("Cache reload already in progress, skipping")
        return False

    async with reload_lock:
        # Clear pending flag
        app_cache.pending_reload = False

        # Double-check after acquiring lock
        if not needs_reload_from_backend():
            return False

        logger.info("Reloading cache from backend...")
        start_time = time.time()

        backend = get_cache_backend()
        cached = await backend.load()
        if cached is None:
            logger.warning("Failed to load cache from backend")
            return False

        # Get current version BEFORE validation so we can update it even on failure
        # This prevents retry loops when a bad cache exists
        current_version = backend.get_version()

        # Validate required data
        if not cached.roles_by_id or not cached.all_operations:
            logger.warning("Loaded cache incomplete, keeping current")
            # Update version to prevent retry loop on same bad data
            app_cache.loaded_cache_version = current_version
            return False

        # Check version - if outdated, need fresh rebuild
        if cached.metadata.version != CACHE_VERSION:
            logger.warning(
                f"Cache version mismatch: {cached.metadata.version} != {CACHE_VERSION}, "
                "keeping current (worker will rebuild)"
            )
            # Update version to prevent retry loop
            app_cache.loaded_cache_version = current_version
            return False

        # Backend contains complete CacheData with all computed fields.
        # Just swap it in - no recomputation needed!
        app_cache.swap(cached)

        app_cache.loaded_cache_version = current_version
        app_cache.is_preloaded = True

        elapsed = time.time() - start_time
        logger.info(
            f"Cache reloaded in {elapsed:.2f}s: {len(cached.roles_by_id)} roles, "
            f"{len(cached.all_operations)} ops, "
            f"{len(cached.role_coverage)} role coverages (precomputed)"
        )
        return True
