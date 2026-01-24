"""In-memory cache container."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from cachetools import LRUCache

from azurerbac.cache.models import CacheData, CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.matching.models import RoleCoverage, RoleNetPermissions
from azurerbac.telemetry import track_cache_hit

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, RoleDefinition
    from azurerbac.web.services.models import RoleAllowingOperation

logger = logging.getLogger(__name__)

# Maximum entries in the allowing_roles cache (roles granting each operation)
# Estimated ~10KB per entry ≈ 50MB max memory
_ALLOWING_ROLES_CACHE_MAX_SIZE: Final[int] = 5000

# Type alias for the allowing_roles_cache value type
RoleAllowingOperationList = list["RoleAllowingOperation"]


class CacheContainer:
    """Thread-safe in-memory cache."""

    __slots__ = (
        "_allowing_roles_cache",
        "_cache",
        "_loaded_version",
        "_pending_reload",
        "_reload_lock",
        "_role_pages",
    )

    def __init__(self) -> None:
        self._cache: CacheData = CacheData()
        self._role_pages: dict[str, list] = {}  # Paginated role listings
        self._allowing_roles_cache: LRUCache[str, RoleAllowingOperationList] = LRUCache(
            maxsize=_ALLOWING_ROLES_CACHE_MAX_SIZE
        )
        self._loaded_version: str | None = None
        self._reload_lock = asyncio.Lock()
        self._pending_reload = False

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
        """Async lock for reload operations."""
        return self._reload_lock

    @property
    def cache(self) -> CacheData:
        """Current cache data."""
        return self._cache

    def swap(self, new_cache: CacheData) -> None:
        """Atomically swap the entire cache."""
        self._cache = new_cache
        self._role_pages.clear()
        self._allowing_roles_cache.clear()
        logger.debug("Cache swapped, role_pages and allowing_roles_cache cleared")

    def get_role_by_id(self, role_id: str) -> CachedRole | None:
        """Get cached role by ID."""
        return self._cache.roles_by_id.get(role_id)

    def get_all_roles(self) -> list[RoleDefinition]:
        return self._cache.get_role_definitions()

    def get_all_operations(self) -> list[OperationData]:
        return self._cache.all_operations

    def get_change_events(self) -> list[CachedChangeEvent]:
        return self._cache.all_change_events

    def get_events_for_role(self, role_id: str) -> list[CachedChangeEvent]:
        """Get change events for a specific role."""
        return [e for e in self._cache.all_change_events if e.role_id == role_id]

    def get_role_coverage(self, role_id: str) -> RoleCoverage | None:
        """Get cached role coverage (control_ops, data_ops) or None if not cached.

        Returns a RoleCoverage NamedTuple with control and data operation sets
        that the role grants, after applying notActions/notDataActions exclusions.
        """
        return self._cache.role_coverage.get(role_id)

    def get_ops_lowered_to_orig(self) -> dict[str, str]:
        """Get mapping from lowered operation name to original casing."""
        return self._cache.ops_lowered_to_orig

    def restore_operation_casing(self, ops: Iterable[str]) -> list[str]:
        """Restore original casing for lowered operation names."""
        ops_map = self._cache.ops_lowered_to_orig
        return [ops_map.get(op, op) for op in ops]

    def get_role_net_permissions(self, role_id: str) -> RoleNetPermissions | None:
        """Get cached role net permissions (control_count, data_count) or None if not cached.

        Returns a RoleNetPermissions NamedTuple with the count of actual operations
        the role grants after applying notActions/notDataActions exclusions.
        """
        return self._cache.role_net_permissions.get(role_id)

    def get_operation_role_count(self, operation_name: str) -> int:
        """Get cached count of roles granting an operation.

        Returns the number of built-in roles that grant the specified operation.
        Uses the pre-computed inverted index.
        """
        return len(self._cache.operation_to_roles.get(operation_name.lower(), []))

    def get_roles_for_operation(self, operation_name: str) -> list[str]:
        """Get role IDs that grant an operation.

        Returns list of role_ids that grant the specified operation.
        Uses the pre-computed inverted index.
        """
        return self._cache.operation_to_roles.get(operation_name.lower(), [])

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

    def get_allowing_roles(self, key: str) -> RoleAllowingOperationList | None:
        result = self._allowing_roles_cache.get(key)
        track_cache_hit("allowing_roles", result is not None, key)
        return result

    def set_allowing_roles(self, key: str, value: RoleAllowingOperationList) -> None:
        self._allowing_roles_cache[key] = value

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
        self._allowing_roles_cache.clear()
        self._loaded_version = None
        self._pending_reload = False
