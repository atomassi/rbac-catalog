"""Cache package for Azure RBAC application."""

from azurerbac.cache.app_cache import AppCache
from azurerbac.cache.models import (
    CACHE_VERSION,
    CacheData,
    CachedRole,
    CacheMetadata,
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.cache.persistence import (
    delete_cache_file,
    get_cache_dir,
    get_cache_file_mtime,
    get_cache_file_path,
    load_cache_from_disk,
    save_cache_to_disk,
)
from azurerbac.cache.precompute import (
    clear_computed_caches,
    precompute_all_caches,
)
from azurerbac.cache.refresh import (
    invalidate_and_rebuild_cache,
    rebuild_cache,
)
from azurerbac.cache.utils import (
    build_operations_prefix_index,
    get_matching_operations,
)

# Singleton app cache instance - the single source of truth for all cached data.
# All data (raw + indexes + computed) is in app_cache.cache (a CacheData object).
app_cache = AppCache()

__all__ = [
    "CACHE_VERSION",
    "AppCache",
    "CacheData",
    "CacheMetadata",
    "CachedRole",
    "app_cache",
    "build_indexes",
    "build_operations_prefix_index",
    "clear_computed_caches",
    "compute_operations_hash",
    "compute_roles_hash",
    "delete_cache_file",
    "get_cache_dir",
    "get_cache_file_mtime",
    "get_cache_file_path",
    "get_matching_operations",
    "invalidate_and_rebuild_cache",
    "load_cache_from_disk",
    "precompute_all_caches",
    "rebuild_cache",
    "save_cache_to_disk",
]
