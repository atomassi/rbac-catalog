"""Cache service - single class managing in-memory cache."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from azurerbac.cache.models import (
    CacheData,
    CachedChangeEvent,
    CachedRole,
    PopularComparison,
    RequestCaches,
    Sitemap,
)
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT, RoleStatus
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.core.singleton import ThreadSafeSingleton
from azurerbac.matching.models import RoleCoverage, RoleNetPermissions
from azurerbac.telemetry import track_cache_call, track_cache_hit

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.azure.models import OperationData, RoleDefinition
    from azurerbac.comparer import RoleComparison
    from azurerbac.web.services.models import RelatedRole, RoleAllowingOperation

logger = logging.getLogger(__name__)

# Lock to prevent concurrent cache rebuilds (async-safe)
_REBUILD_LOCK: Final[asyncio.Lock] = asyncio.Lock()

# Type alias for the allowing_roles_cache value type
RoleAllowingOperationList = list["RoleAllowingOperation"]
RelatedRoleList = list["RelatedRole"]


class CacheService:
    """Thread-safe in-memory cache service.

    Manages CacheData (immutable snapshot) and RequestCaches (mutable LRU caches).
    RequestCaches are cleared on swap() when new data is loaded.

    Simple architecture:
    - Startup: build from DB, swap into memory
    - Periodic: rebuild from DB every 2 hours
    - No file caching, no worker process coordination
    """

    __slots__ = ("_cache", "_request_caches")

    def __init__(self) -> None:
        self._cache: CacheData = CacheData()
        self._request_caches: RequestCaches = RequestCaches()

    # -------------------------------------------------------------------------
    # Core cache access
    # -------------------------------------------------------------------------

    @property
    def cache(self) -> CacheData:
        return self._cache

    def swap(self, new_cache: CacheData) -> None:
        """Atomically swap the entire cache and clear request caches."""
        self._cache = new_cache
        self._request_caches = RequestCaches()
        logger.debug("Cache swapped (data replaced, request caches cleared)")

    def reset(self) -> None:
        """Reset in-memory cache to empty state."""
        self._cache = CacheData()
        self._request_caches = RequestCaches()

    # -------------------------------------------------------------------------
    # DB rebuild operations
    # -------------------------------------------------------------------------

    async def build_from_db(self, session: AsyncSession) -> CacheData:
        from azurerbac.cache.build import build_from_db

        return await build_from_db(session)

    def swap_in_memory(self, cache_data: CacheData) -> None:
        self.swap(cache_data)

    async def rebuild_in_memory(self, session: AsyncSession) -> bool:
        """Build cache from DB and swap into memory. Skips if already in progress."""
        if _REBUILD_LOCK.locked():
            logger.warning("Cache rebuild already in progress, skipping")
            return False

        async with _REBUILD_LOCK:
            try:
                cache_data = await self.build_from_db(session)
                self._initialize_ai_recommender(cache_data)
                self.swap(cache_data)
                self._seed_popular_comparisons()
                return True
            except Exception as e:
                logger.exception("Failed to rebuild cache in memory: %s", e)
                return False

    def _initialize_ai_recommender(self, cache_data: CacheData) -> None:
        """Re-initialize AI recommender with updated role data."""
        try:
            from azurerbac.airecommender import get_ai_recommender

            ai_recommender = get_ai_recommender()
            active_roles = [
                cached_role.definition
                for cached_role in cache_data.roles_by_id.values()
                if cached_role.status == RoleStatus.ACTIVE
            ]
            ai_recommender.initialize(active_roles)
            logger.info("AI recommender re-initialized with updated roles")
        except Exception as e:
            logger.exception("Failed to re-initialize AI recommender: %s", e)

    def _seed_popular_comparisons(self) -> None:
        """Pre-compute comparison results for popular role pairs.

        Warms the comparisons LRU cache so popular pairs are instant
        on first request after startup or cache refresh.
        """
        from azurerbac.comparer import compute_role_comparison

        if not (popular := self._cache.popular_comparisons):
            return

        seeded = 0
        for pair in popular:
            if compute_role_comparison(pair.role_a_id, pair.role_b_id, cache=self) is not None:
                seeded += 1

        logger.info("Seeded %d/%d popular comparisons into cache", seeded, len(popular))

    # -------------------------------------------------------------------------
    # Role accessors
    # -------------------------------------------------------------------------

    def get_role_by_id(self, role_id: str) -> CachedRole | None:
        result = self._cache.roles_by_id.get(role_id)
        track_cache_hit("role_by_id", result is not None, role_id)
        return result

    def get_all_roles(self) -> list[RoleDefinition]:
        result = self._cache.role_definitions
        track_cache_call("get_all_roles")
        return result

    def get_role_coverage(self, role_id: str) -> RoleCoverage | None:
        result = self._cache.role_coverage.get(role_id)
        track_cache_hit("role_coverage", result is not None, role_id)
        return result

    def get_role_net_permissions(self, role_id: str) -> RoleNetPermissions | None:
        result = self._cache.role_net_permissions.get(role_id)
        track_cache_hit("role_net_permissions", result is not None, role_id)
        return result

    def get_roles_for_operation(self, operation_name: str) -> list[str]:
        key = operation_name.lower()
        result = self._cache.operation_to_roles.get(key)
        track_cache_hit("operation_to_roles", result is not None, key)
        return result if result is not None else []

    def get_operation_role_count(self, operation_name: str) -> int:
        key = operation_name.lower()
        return len(self._cache.operation_to_roles.get(key, []))

    # -------------------------------------------------------------------------
    # Operation accessors
    # -------------------------------------------------------------------------

    def get_all_operations(self) -> list[OperationData]:
        result = self._cache.all_operations
        track_cache_call("get_all_operations")
        return result

    def restore_operation_casing(self, ops: Iterable[str]) -> list[str]:
        """Restore original casing for lowered operation names."""
        ops_map = self._cache.ops_lowered_to_orig
        return [ops_map.get(op, op) for op in ops]

    def search_operations(
        self, query: str, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[OperationData]:
        """Search operations using pre-built indexes."""
        cache = self._cache
        if not cache.ops_by_name_lower:
            track_cache_call("search_operations", query=query, results=0)
            return []

        q_lower = query.lower()

        if is_wildcard_pattern(query):
            if "/" in q_lower:
                prefix = q_lower.partition("/")[0]
                source = cache.ops_by_prefix.get(prefix, list(cache.ops_by_name_lower.values()))
            else:
                source = list(cache.ops_by_name_lower.values())
            matching = [op for op in source if fnmatch.fnmatch(op.name_lower, q_lower)]
        else:
            matching = [op for op in cache.ops_by_name_lower.values() if op.matches_search(q_lower)]

        matching.sort(key=lambda x: x.name)
        result = matching[:limit]
        track_cache_call("search_operations", query=query, results=len(result))
        return result

    def count_wildcard_matches(self, pattern: str, is_data_action: bool = False) -> int:
        """Count operations matching a wildcard pattern.

        Uses the same lowered/deduplicated operation sets as the recommendation
        service to ensure consistent counts between the UI and role matching.
        """
        cache = self._cache
        if not cache.all_operations:
            return 0

        # Use cached pattern compilation for regex lookup
        from azurerbac.core.patterns import pattern_to_regex

        regex = pattern_to_regex(pattern)
        source = cache.data_ops_lowered if is_data_action else cache.control_ops_lowered

        return sum(1 for op in source if regex.match(op))

    # -------------------------------------------------------------------------
    # Change events
    # -------------------------------------------------------------------------

    def get_change_events(self) -> list[CachedChangeEvent]:
        return self._cache.all_change_events

    def get_events_for_role(self, role_id: str) -> list[CachedChangeEvent]:
        """Get change events for a specific role."""
        return self._cache.events_by_role.get(role_id, [])

    def get_filtered_events(self, cache_key: str) -> list[CachedChangeEvent] | None:
        result = self._request_caches.filtered_events.get(cache_key)
        track_cache_hit("filtered_events", result is not None, cache_key)
        return result

    def set_filtered_events(self, cache_key: str, events: list[CachedChangeEvent]) -> None:
        self._request_caches.filtered_events[cache_key] = events

    # -------------------------------------------------------------------------
    # Sitemap
    # -------------------------------------------------------------------------

    def get_sitemap(self) -> Sitemap | None:
        return self._cache.sitemap

    def get_popular_comparisons(self) -> list[PopularComparison]:
        return self._cache.popular_comparisons

    # -------------------------------------------------------------------------
    # Request-scoped caches (role pages, operation pages, allowing roles)
    # -------------------------------------------------------------------------

    def get_role_page(self, page_key: str) -> Any:
        result = self._request_caches.role_pages.get(page_key)
        track_cache_hit("role_page", result is not None, page_key)
        return result

    def set_role_page(self, page_key: str, roles: Any) -> None:
        self._request_caches.role_pages[page_key] = roles

    def get_operation_page(self, page_key: str) -> Any:
        result = self._request_caches.operation_pages.get(page_key)
        track_cache_hit("operation_page", result is not None, page_key)
        return result

    def set_operation_page(self, page_key: str, value: Any) -> None:
        self._request_caches.operation_pages[page_key] = value

    def get_allowing_roles(self, key: str) -> RoleAllowingOperationList | None:
        result = self._request_caches.allowing_roles.get(key)
        track_cache_hit("allowing_roles", result is not None, key)
        return result

    def set_allowing_roles(self, key: str, value: RoleAllowingOperationList) -> None:
        self._request_caches.allowing_roles[key] = value

    def get_related_roles(self, key: str) -> RelatedRoleList | None:
        result = self._request_caches.related_roles.get(key)
        track_cache_hit("related_roles", result is not None, key)
        return result

    def set_related_roles(self, key: str, value: RelatedRoleList) -> None:
        self._request_caches.related_roles[key] = value

    def get_comparison(self, key: str) -> RoleComparison | None:
        result = self._request_caches.comparisons.get(key)
        track_cache_hit("comparisons", result is not None, key)
        return result

    def set_comparison(self, key: str, value: RoleComparison) -> None:
        self._request_caches.comparisons[key] = value

    # -------------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------------

    def set_metadata(
        self,
        *,
        unique_providers: list[str] | None = None,
        last_scan: datetime | None = None,
        first_scan: datetime | None = None,
    ) -> None:
        """Set metadata fields on the source data."""
        updates = {}
        if unique_providers is not None:
            updates["unique_providers"] = unique_providers
        if last_scan is not None:
            updates["last_scan"] = last_scan
        if first_scan is not None:
            updates["first_scan"] = first_scan
        if updates:
            new_source = replace(self._cache.source, **updates)
            self._cache = replace(self._cache, source=new_source)


_service_singleton: ThreadSafeSingleton[CacheService] = ThreadSafeSingleton(CacheService)


def get_cache_service() -> CacheService:
    """Get the global cache service instance (thread-safe singleton)."""
    return _service_singleton.get()
