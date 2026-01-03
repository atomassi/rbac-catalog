"""Disk persistence for cache data."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import TYPE_CHECKING

from azurerbac.settings import Settings, is_running_in_azure

if TYPE_CHECKING:
    from azurerbac.cache.models import CacheData

logger = logging.getLogger(__name__)


def _get_cache_dir() -> Path:
    """Get the cache directory, creating it if needed."""
    settings = Settings.get()

    # Allow override via settings (from CACHE_DIR env var)
    if settings.cache_dir:
        cache_dir = Path(settings.cache_dir)
    elif is_running_in_azure():
        # Azure App Service - use /home for persistence across restarts
        cache_dir = Path("/home/cache")
    else:
        # Local development - azurerbac/.cache
        cache_dir = Path(__file__).parent.parent / ".cache"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_cache_file() -> Path:
    """Get the path to the cache file."""
    return _get_cache_dir() / "app_cache.pkl"


def get_cache_file_path() -> Path:
    """Get the path to the cache file."""
    return _get_cache_file()


def save_cache_to_disk(data: CacheData) -> bool:
    """Save cache data to disk. Returns True if successful.

    The complete CacheData is saved including all computed fields.
    On reload, no recomputation is needed.
    """
    # Validate cache has data before saving (prevent saving empty cache)
    if not data.roles_by_id or not data.all_operations:
        logger.warning(
            "Refusing to save incomplete cache: %d roles, %d ops",
            len(data.roles_by_id),
            len(data.all_operations),
        )
        return False

    try:
        # Write to temp file first, then rename (atomic)
        cache_file = _get_cache_file()
        temp_file = cache_file.with_suffix(".tmp")
        with open(temp_file, "wb") as f:
            pickle.dump(data, f)
        temp_file.rename(cache_file)
        logger.info("Saved cache to disk: %s", cache_file)
        return True
    except Exception as e:
        logger.exception("Failed to save cache to disk: %s", e)
        return False


def load_cache_from_disk() -> CacheData | None:
    """Load cache from disk. Returns None if no valid cache exists.

    Returns the complete CacheData with all computed fields.
    No recomputation is needed after loading.
    """
    cache_file = _get_cache_file()
    if not cache_file.exists():
        logger.info("No cache file found")
        return None

    try:
        with open(cache_file, "rb") as f:
            data = pickle.load(f)
        logger.info("Loaded cache from disk: %s", cache_file)
        logger.info(
            "  Roles: %d, Operations: %d",
            data.metadata.roles_count,
            data.metadata.operations_count,
        )
        return data
    except Exception as e:
        logger.exception("Failed to load cache from disk: %s", e)
        return None


def delete_cache_file() -> None:
    """Delete the cache file from disk."""
    cache_file = _get_cache_file()
    if cache_file.exists():
        try:
            cache_file.unlink()
            logger.info("Deleted cache file")
        except Exception as e:
            logger.exception("Failed to delete cache file: %s", e)


def get_cache_file_mtime() -> float | None:
    """Get the modification time of the cache file.

    Returns:
        The mtime as a float (seconds since epoch), or None if file doesn't exist.
    """
    cache_file = _get_cache_file()
    if cache_file.exists():
        try:
            return cache_file.stat().st_mtime
        except OSError:
            return None
    return None
