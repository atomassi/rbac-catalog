"""In-memory cache container.

Pure in-memory container that holds the CacheData.
No I/O, no sync logic - just swap() and accessors.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from azurerbac.cache.models import CacheData, CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.telemetry import track_cache_hit

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, RoleDefinition

logger = logging.getLogger(__name__)


class CacheContainer:
    """Thread-safe in-memory cache with atomic refresh.

    All data is stored in a single CacheData object that can be atomically
    swapped. This prevents race conditions where readers see partially
    updated data during refresh.

    Usage:
        # Read data (capture reference for consistent reads)
        cache = container.cache
        ops = cache.all_operations
        coverage = cache.role_coverage.get(role_id)

        # Atomic refresh (called by build.py)
        new_cache = CacheData(...)
        container.swap(new_cache)
    """

    __slots__ = (
        "_cache",
        "_loaded_version",
        "_misc_cache",
        "_pending_reload",
        "_reload_lock",
        "_role_pages",
    )

    def __init__(self) -> None:
        self._cache: CacheData = CacheData()
        self._role_pages: dict[str, list] = {}  # Paginated role listings
        self._misc_cache: dict[str, Any] = {}  # Dynamic key-value cache
        self._loaded_version: str | None = None
        self._reload_lock = asyncio.Lock()
        self._pending_reload = False

    # ─────────────────────────────────────────────────────────────────────────
    # Public accessors for internal state
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def loaded_version(self) -> str | None:
        return self._loaded_version

    @loaded_version.setter
    def loaded_version(self, version: str | None) -> None:
        self._loaded_version = version

    @property
    def pending_reload(self) -> bool:
        return self._pending_reload

    @pending_reload.setter
    def pending_reload(self, value: bool) -> None:
        self._pending_reload = value

    @property
    def reload_lock(self) -> asyncio.Lock:
        """Async lock for reload operations (used by build.py)."""
        return self._reload_lock

    # ─────────────────────────────────────────────────────────────────────────
    # Cache access (atomic)
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def cache(self) -> CacheData:
        """Current cache data. Capture reference for consistent reads."""
        return self._cache

    def swap(self, new_cache: CacheData) -> None:
        """Atomically swap the entire cache (thread-safe via GIL).

        Also clears misc_cache since cached lookups (like roles_allowing_op)
        depend on the computed data and must be recalculated.
        """
        self._cache = new_cache
        self._misc_cache.clear()
        logger.debug("Cache swapped, misc_cache cleared")

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience accessors
    # ─────────────────────────────────────────────────────────────────────────

    def get_role_by_id(self, role_id: str) -> CachedRole | None:
        """Get a cached role by ID.

        Returns CachedRole which contains both the RoleDefinition and DB metadata.
        """
        result = self._cache.roles_by_id.get(role_id)
        track_cache_hit("role", result is not None, role_id)
        return result

    def get_all_roles(self) -> list[RoleDefinition]:
        return self._cache.get_role_definitions()

    def get_all_operations(self) -> list[OperationData]:
        return self._cache.all_operations

    def get_change_events(self) -> list[CachedChangeEvent]:
        return self._cache.all_change_events

    def get_events_for_role(self, role_id: str) -> list[CachedChangeEvent]:
        """Get change events for a specific role."""
        return [e for e in self._cache.all_change_events if e.role_id == role_id]

    def get_role_coverage(self, role_id: str) -> tuple[set[str], set[str]] | None:
        """Get cached role coverage (control_ops, data_ops) or None if not cached.

        Returns a tuple of (control_operations_set, data_operations_set)
        that the role grants, after applying notActions/notDataActions exclusions.
        """
        return self._cache.role_coverage.get(role_id)

    def get_role_net_permissions(self, role_id: str) -> tuple[int, int] | None:
        """Get cached role net permissions (control_count, data_count) or None if not cached.

        Returns the count of actual operations the role grants after applying
        notActions/notDataActions exclusions.
        """
        return self._cache.role_net_permissions.get(role_id)

    def get_operation_role_count(self, operation_name: str) -> int:
        """Get cached count of roles granting an operation.

        Returns the number of built-in roles that grant the specified operation.
        Uses the pre-computed role coverage cache.
        """
        return self._cache.operation_role_count.get(operation_name.lower(), 0)

    # ─────────────────────────────────────────────────────────────────────────
    # Role pages (paginated listings - separate from main cache)
    # ─────────────────────────────────────────────────────────────────────────

    def get_role_page(self, page_key: str) -> Any:
        """Get a cached role page or count value."""
        result = self._role_pages.get(page_key)
        track_cache_hit("role_page", result is not None, page_key)
        return result

    def set_role_page(self, page_key: str, roles: Any) -> None:
        """Cache a role page or count value."""
        self._role_pages[page_key] = roles

    def get_role_pages_count(self) -> int:
        return len(self._role_pages)

    # ─────────────────────────────────────────────────────────────────────────
    # Misc key-value cache (for dynamic caches like roles_allowing_op:*)
    # ─────────────────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        result = self._misc_cache.get(key)
        track_cache_hit("misc", result is not None, key)
        return result

    def set(self, key: str, value: Any) -> None:
        self._misc_cache[key] = value

    def set_metadata(
        self,
        *,
        unique_providers: list[str] | None = None,
        last_scan: datetime | None = None,
        first_scan: datetime | None = None,
    ) -> None:
        """Set metadata fields."""
        updates = {}
        if unique_providers is not None:
            updates["unique_providers"] = unique_providers
        if last_scan is not None:
            updates["last_scan"] = last_scan
        if first_scan is not None:
            updates["first_scan"] = first_scan
        if updates:
            self._cache = replace(self._cache, **updates)

    # ─────────────────────────────────────────────────────────────────────────
    # Search operations
    # ─────────────────────────────────────────────────────────────────────────

    def search_operations(
        self, query: str, limit: int = DEFAULT_SEARCH_LIMIT, is_wildcard: bool = False
    ) -> list[OperationData]:
        """Search operations using pre-built indexes."""
        cache = self._cache
        if not cache.ops_by_name_lower:
            return []

        q_lower = query.lower()

        if is_wildcard:
            if "/" in q_lower:
                prefix = q_lower.split("/")[0]
                source = cache.ops_by_prefix.get(prefix, list(cache.ops_by_name_lower.values()))
            else:
                source = list(cache.ops_by_name_lower.values())
            matching = [op for op in source if fnmatch.fnmatch(op.name.lower(), q_lower)]
        else:
            matching = [op for op in cache.ops_by_name_lower.values() if op.matches_search(q_lower)]

        matching.sort(key=lambda x: x.name)
        return matching[:limit]

    def count_wildcard_matches(self, pattern: str, is_data_action: bool = False) -> int:
        """Count operations matching a wildcard pattern."""
        cache = self._cache
        if not cache.all_operations:
            return 0

        # Use cached pattern compilation for O(1) regex lookup
        from azurerbac.core.patterns import pattern_to_regex

        regex = pattern_to_regex(pattern)

        return sum(
            1
            for op in cache.all_operations
            if op.is_data_action == is_data_action and regex.match(op.name)
        )

    def reset(self) -> None:
        """Reset in-memory cache to empty state."""
        self._cache = CacheData()
        self._role_pages.clear()
        self._misc_cache.clear()
        self._loaded_version = None
        self._pending_reload = False
