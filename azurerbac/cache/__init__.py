"""Cache package for Azure RBAC."""

from azurerbac.cache.build import precompute_all
from azurerbac.cache.models import (
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    PatternCacheKey,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.cache.service import CacheService, get_cache_service

__all__ = [
    "CacheData",
    "CacheMetadata",
    "CacheService",
    "CachedChangeEvent",
    "CachedRole",
    "PatternCacheKey",
    "compute_operations_hash",
    "compute_roles_hash",
    "get_cache_service",
    "precompute_all",
]
