"""Cache package for Azure RBAC."""

from azurerbac.cache.backends import (
    CACHE_FILENAME,
    CacheBackend,
    CacheBackendType,
    CacheFileWatcher,
    FileCacheBackend,
    get_cache_watcher,
)
from azurerbac.cache.build import (
    build_from_db,
    build_operations_prefix_index,
    get_matching_operations,
    precompute_all,
)
from azurerbac.cache.container import CacheContainer
from azurerbac.cache.models import (
    CACHE_VERSION,
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    PatternCacheKey,
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.cache.serialization import deserialize_from_bytes, serialize_to_bytes
from azurerbac.cache.service import CacheService, get_cache_service
from azurerbac.matching.models import Plane

__all__ = [
    "CACHE_FILENAME",
    "CACHE_VERSION",
    "CacheBackend",
    "CacheBackendType",
    "CacheContainer",
    "CacheData",
    "CacheFileWatcher",
    "CacheMetadata",
    "CacheService",
    "CachedChangeEvent",
    "CachedRole",
    "FileCacheBackend",
    "PatternCacheKey",
    "Plane",
    "build_from_db",
    "build_indexes",
    "build_operations_prefix_index",
    "compute_operations_hash",
    "compute_roles_hash",
    "deserialize_from_bytes",
    "get_cache_service",
    "get_cache_watcher",
    "get_matching_operations",
    "precompute_all",
    "serialize_to_bytes",
]
