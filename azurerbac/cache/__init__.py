"""Cache package for Azure RBAC application.

Architecture:
    - models.py:       Data classes
    - serialization.py: Msgpack serialization
    - container.py:    In-memory singleton (CacheContainer)
    - build.py:        All cache building & refresh operations
    - backends/:       Storage abstraction (file-based by default)
"""

from azurerbac.cache.backends import (
    CACHE_FILENAME,
    CacheBackend,
    CacheBackendType,
    CacheFileWatcher,
    FileCacheBackend,
    get_cache_backend,
    get_cache_watcher,
)
from azurerbac.cache.build import (
    build_from_db,
    build_operations_prefix_index,
    get_matching_operations,
    invalidate_all,
    invalidate_and_rebuild,
    mark_pending_reload,
    precompute_all,
    rebuild_and_save,
    rebuild_in_memory,
    reload_if_needed,
    save,
    swap_in_memory,
)
from azurerbac.cache.container import CacheContainer, get_cache_container
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

__all__ = [
    # Backends
    "CACHE_FILENAME",
    # Models
    "CACHE_VERSION",
    "CacheBackend",
    "CacheBackendType",
    # Container
    "CacheContainer",
    "CacheData",
    "CacheFileWatcher",
    "CacheMetadata",
    "CachedChangeEvent",
    "CachedRole",
    "FileCacheBackend",
    # Build operations
    "build_from_db",
    "build_indexes",
    "build_operations_prefix_index",
    "compute_operations_hash",
    "compute_roles_hash",
    # Serialization
    "deserialize_from_bytes",
    "get_cache_backend",
    "get_cache_container",
    "get_cache_watcher",
    "get_matching_operations",
    "invalidate_all",
    "invalidate_and_rebuild",
    "mark_pending_reload",
    "precompute_all",
    "rebuild_and_save",
    "rebuild_in_memory",
    "reload_if_needed",
    "save",
    "serialize_to_bytes",
    "swap_in_memory",
]
