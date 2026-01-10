"""Cache backend abstraction for pluggable storage.

DEPRECATED: Import from azurerbac.cache.backends instead.

This module re-exports from azurerbac.cache.backends for backward compatibility.
"""

# Re-export everything from the new location
from azurerbac.cache.backends import (
    CACHE_FILENAME,
    CacheBackend,
    FileCacheBackend,
    get_cache_backend,
)

__all__ = [
    "CACHE_FILENAME",
    "CacheBackend",
    "FileCacheBackend",
    "get_cache_backend",
]
