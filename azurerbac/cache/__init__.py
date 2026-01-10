"""Cache package for Azure RBAC application.

Architecture:
    - models.py:       Data classes (CacheData, CachedRole, etc.)
    - serialization.py: Msgpack serialization
    - container.py:    In-memory cache container (CacheContainer)
    - service.py:      Orchestrator (CacheService) - THE singleton entry point
    - build.py:        Pure computation functions (precompute_all, build_from_db)
    - backends/:       Storage abstraction (FileCacheBackend, CacheFileWatcher)

Usage:
    from azurerbac.cache import get_cache_service

    service = get_cache_service()
    role = service.container.get_role_by_id(role_id)
    ops = service.container.get_all_operations()
    await service.reload_if_needed()
"""

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
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.cache.serialization import (
    deserialize_from_bytes,
    serialize_to_bytes,
)
from azurerbac.cache.service import CacheService, get_cache_service

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
