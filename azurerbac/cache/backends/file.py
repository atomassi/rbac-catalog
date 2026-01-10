"""File-based cache backend using msgpack and watchdog."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from azurerbac.cache.backends.base import CacheBackend
from azurerbac.settings import Settings, is_running_in_azure

if TYPE_CHECKING:
    from azurerbac.cache.models import CacheData

logger = logging.getLogger(__name__)

# Cache file name constant
CACHE_FILENAME: Final[str] = "app_cache.msgpack"


class FileCacheBackend(CacheBackend):
    """File-based cache backend using msgpack and watchdog.

    Stores cache as a single msgpack file on disk.
    Uses watchdog for instant change detection (no polling).
    Thread pool for non-blocking file I/O.
    """

    def __init__(self) -> None:
        """Initialize the file cache backend."""
        self._cache_dir: Path | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._watcher_started = False

    @property
    def cache_dir(self) -> Path:
        """Get the cache directory path."""
        return self._get_cache_dir()

    @cache_dir.setter
    def cache_dir(self, value: Path) -> None:
        """Set the cache directory path (for testing)."""
        self._cache_dir = value
        value.mkdir(parents=True, exist_ok=True)

    @property
    def cache_file(self) -> Path:
        """Get the cache file path."""
        return self._get_cache_file()

    def _get_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create the thread pool executor."""
        if self._executor is None:
            self._executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="cache_io"
            )
        return self._executor

    def _get_cache_dir(self) -> Path:
        """Get the cache directory, creating if needed."""
        if self._cache_dir is not None:
            return self._cache_dir

        settings = Settings.get()

        if settings.cache_dir:
            self._cache_dir = Path(settings.cache_dir)
        elif is_running_in_azure():
            self._cache_dir = Path("/home/cache")
        else:
            self._cache_dir = Path(__file__).parent.parent.parent / ".cache"

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir

    def _get_cache_file(self) -> Path:
        """Get the path to the cache file."""
        return self._get_cache_dir() / CACHE_FILENAME

    def _save_sync(self, data: CacheData) -> bool:
        """Save cache to disk (sync, runs in thread pool)."""
        if not data.roles_by_id or not data.all_operations:
            logger.warning(
                "Refusing to save incomplete cache: %d roles, %d ops",
                len(data.roles_by_id),
                len(data.all_operations),
            )
            return False

        try:
            from dataclasses import asdict

            from azurerbac.cache.serialization import serialize_to_bytes

            cache_file = self._get_cache_file()
            temp_file = cache_file.with_suffix(".tmp")

            data_dict = asdict(data)

            # Convert nested dataclasses/models to dicts
            if data.roles_by_id:
                data_dict["roles_by_id"] = {
                    role_id: role.to_dict() for role_id, role in data.roles_by_id.items()
                }
            if data.all_operations:
                data_dict["all_operations"] = [op.to_dict() for op in data.all_operations]
            if data.ops_by_name_lower:
                data_dict["ops_by_name_lower"] = {
                    k: v.to_dict() for k, v in data.ops_by_name_lower.items()
                }
            if data.ops_by_prefix:
                data_dict["ops_by_prefix"] = {
                    k: [op.to_dict() for op in v] for k, v in data.ops_by_prefix.items()
                }
            if data.all_change_events:
                data_dict["all_change_events"] = [ev.to_dict() for ev in data.all_change_events]

            packed = serialize_to_bytes(data_dict)

            with open(temp_file, "wb") as f:
                f.write(packed)

            # Atomic rename: readers see either old or new file, never partial
            os.replace(temp_file, cache_file)
            logger.info("Cache saved to disk: %s", cache_file)
            return True
        except Exception as e:
            logger.exception("Failed to save cache: %s", e)
            return False

    def _load_sync(self) -> CacheData | None:
        """Load cache from disk (sync, runs in thread pool)."""
        cache_file = self._get_cache_file()
        if not cache_file.exists():
            logger.debug("No cache file found at %s", cache_file)
            return None

        try:
            from azurerbac.azure.models import OperationData
            from azurerbac.cache.models import (
                CacheData,
                CachedChangeEvent,
                CachedRole,
                CacheMetadata,
            )
            from azurerbac.cache.serialization import deserialize_from_bytes

            with open(cache_file, "rb") as f:
                packed = f.read()

            data_dict = deserialize_from_bytes(packed)

            metadata = CacheMetadata(**data_dict.pop("metadata", {}))

            # Reconstruct typed objects from dicts
            if roles_raw := data_dict.get("roles_by_id"):
                data_dict["roles_by_id"] = {
                    role_id: CachedRole.from_dict(role_data)
                    for role_id, role_data in roles_raw.items()
                }
            if ops_raw := data_dict.get("all_operations"):
                data_dict["all_operations"] = [OperationData.model_validate(op) for op in ops_raw]
            if ops_by_name_raw := data_dict.get("ops_by_name_lower"):
                data_dict["ops_by_name_lower"] = {
                    k: OperationData.model_validate(v) for k, v in ops_by_name_raw.items()
                }
            if ops_by_prefix_raw := data_dict.get("ops_by_prefix"):
                data_dict["ops_by_prefix"] = {
                    k: [OperationData.model_validate(op) for op in v]
                    for k, v in ops_by_prefix_raw.items()
                }
            if events_raw := data_dict.get("all_change_events"):
                data_dict["all_change_events"] = [
                    CachedChangeEvent.from_dict(ev) for ev in events_raw
                ]

            # Convert lists back to sets/tuples where needed
            if role_coverage := data_dict.get("role_coverage"):
                data_dict["role_coverage"] = {
                    k: (
                        set(v[0]) if isinstance(v[0], list) else v[0],
                        set(v[1]) if isinstance(v[1], list) else v[1],
                    )
                    for k, v in role_coverage.items()
                }
            if role_net_perms := data_dict.get("role_net_permissions"):
                data_dict["role_net_permissions"] = {k: tuple(v) for k, v in role_net_perms.items()}
            if partial_cov := data_dict.get("partial_coverage"):
                data_dict["partial_coverage"] = {k: tuple(v) for k, v in partial_cov.items()}

            data = CacheData(metadata=metadata, **data_dict)
            logger.info(
                "Cache loaded: %d roles, %d operations",
                len(data.roles_by_id),
                len(data.all_operations),
            )
            return data
        except Exception as e:
            logger.exception("Failed to load cache: %s", e)
            return None

    async def save(self, data: CacheData) -> bool:
        """Save cache data to disk asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_executor(), self._save_sync, data)

    async def load(self) -> CacheData | None:
        """Load cache data from disk asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._get_executor(), self._load_sync)

    async def delete(self) -> None:
        """Delete the cache file asynchronously."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._get_executor(), self._delete_sync)

    def _delete_sync(self) -> None:
        """Delete the cache file (internal, runs in thread pool)."""
        cache_file = self._get_cache_file()
        if cache_file.exists():
            try:
                cache_file.unlink()
                logger.info("Cache file deleted")
            except Exception as e:
                logger.exception("Failed to delete cache file: %s", e)

    def exists(self) -> bool:
        """Check if cache file exists."""
        return self._get_cache_file().exists()

    def get_version(self) -> str | None:
        """Get file mtime as version string for change detection."""
        cache_file = self._get_cache_file()
        if cache_file.exists():
            try:
                return str(cache_file.stat().st_mtime)
            except OSError:
                return None
        return None

    def subscribe(self, callback: Callable[[], None]) -> bool:
        """Start watching for cache file changes using watchdog."""
        if self._watcher_started:
            return True

        from azurerbac.cache.backends.watcher import get_cache_watcher

        watcher = get_cache_watcher()
        if watcher.start(self._get_cache_dir(), CACHE_FILENAME, callback):
            self._watcher_started = True
            return True
        return False

    def unsubscribe(self) -> None:
        """Stop watching for cache file changes."""
        if not self._watcher_started:
            return

        from azurerbac.cache.backends.watcher import get_cache_watcher

        get_cache_watcher().stop()
        self._watcher_started = False

    @property
    def is_subscribed(self) -> bool:
        """Check if file watcher is active."""
        return self._watcher_started

    async def close(self) -> None:
        """Stop watcher and shut down executor."""
        self.unsubscribe()
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
