"""Cache backend implementations.

This package provides pluggable storage backends for the cache system.

Current:
    - FileCacheBackend: Local file with watchdog for change detection.

Future options:
    - RedisCacheBackend: Redis with pub/sub for distributed deployments.
    - MemcachedBackend: Simple key-value (requires polling for invalidation).
"""

from azurerbac.cache.backends.base import (
    CacheBackend,
    CacheBackendType,
    get_cache_backend,
)
from azurerbac.cache.backends.file import CACHE_FILENAME, FileCacheBackend
from azurerbac.cache.backends.watcher import CacheFileWatcher, get_cache_watcher

__all__ = [
    "CACHE_FILENAME",
    "CacheBackend",
    "CacheBackendType",
    "CacheFileWatcher",
    "FileCacheBackend",
    "get_cache_backend",
    "get_cache_watcher",
]
