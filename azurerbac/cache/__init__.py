"""Cache package for Azure RBAC."""

from azurerbac.cache.build import (
    build_from_db,
    build_operations_prefix_index,
    get_matching_operations,
    precompute_all,
)
from azurerbac.cache.models import (
    CACHE_VERSION,
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    PatternCacheKey,
    Sitemap,
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.cache.service import CacheService, get_cache_service
from azurerbac.matching.models import Plane

__all__ = [
    "CACHE_VERSION",
    "CacheData",
    "CacheMetadata",
    "CacheService",
    "CachedChangeEvent",
    "CachedRole",
    "PatternCacheKey",
    "Plane",
    "Sitemap",
    "build_from_db",
    "build_indexes",
    "build_operations_prefix_index",
    "compute_operations_hash",
    "compute_roles_hash",
    "get_cache_service",
    "get_matching_operations",
    "precompute_all",
]
