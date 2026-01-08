"""In-memory application cache."""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Self

from azurerbac.cache.models import (
    CACHE_VERSION,
    CacheData,
    CachedChangeEvent,
    CachedRole,
    build_indexes,
)
from azurerbac.cache.persistence import (
    delete_cache_file,
    get_cache_file_mtime,
    load_cache_from_disk,
)
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.telemetry import track_cache_hit

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, RoleDefinition

logger = logging.getLogger(__name__)


class AppCache:
    """Thread-safe in-memory cache with atomic refresh (Singleton).

    All data is stored in a single CacheData object that can be atomically
    swapped. This prevents race conditions where readers see partially
    updated data during refresh.

    This class implements the Singleton pattern - calling AppCache() always
    returns the same instance.

    Usage:
        # Read data (capture reference for consistent reads)
        cache = app_cache.cache
        ops = cache.all_operations
        coverage = cache.role_coverage.get(role_id)

        # Atomic refresh
        new_cache = CacheData(...)
        app_cache.swap(new_cache)
    """

    _instance: AppCache | None = None
    _init_lock = threading.Lock()

    def __new__(cls) -> Self:
        """Ensure only one instance exists (Singleton pattern)."""
        if cls._instance is None:
            with cls._init_lock:
                # Double-check locking for thread safety
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance  # type: ignore[return-value]

    def __init__(self) -> None:
        # Only initialize once (singleton may call __init__ multiple times)
        if getattr(self, "_initialized", False):
            return

        from azurerbac.settings import Settings

        self._cache: CacheData = CacheData()
        self._role_pages: dict[str, list] = {}  # Paginated role listings
        self._misc_cache: dict[str, Any] = {}  # Dynamic key-value cache (roles_allowing_op, etc.)
        self._preloaded = False
        self._loaded_cache_mtime: float | None = None
        self._last_cache_check: float = 0
        self._reload_lock = threading.Lock()  # Prevents concurrent reloads
        self._cache_check_interval = Settings.get().cache_check_interval_seconds
        self._initialized = True

    # ─────────────────────────────────────────────────────────────────────────
    # Public accessors for internal state
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def is_preloaded(self) -> bool:
        """Check if cache has been preloaded."""
        return self._preloaded

    @is_preloaded.setter
    def is_preloaded(self, value: bool) -> None:
        """Mark cache as preloaded."""
        self._preloaded = value

    @property
    def loaded_cache_mtime(self) -> float | None:
        """Get the mtime of the currently loaded cache file."""
        return self._loaded_cache_mtime

    @loaded_cache_mtime.setter
    def loaded_cache_mtime(self, mtime: float | None) -> None:
        """Set the mtime of the currently loaded cache file."""
        self._loaded_cache_mtime = mtime

    @property
    def last_cache_check(self) -> float:
        """Get timestamp of last cache file check."""
        return self._last_cache_check

    @last_cache_check.setter
    def last_cache_check(self, timestamp: float) -> None:
        """Set timestamp of last cache file check."""
        self._last_cache_check = timestamp

    # ─────────────────────────────────────────────────────────────────────────
    # Cache access (atomic)
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def cache(self) -> CacheData:
        """Get current cache. Capture reference for consistent reads."""
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
        """Get all roles as RoleDefinition objects."""
        return self._cache.get_role_definitions()

    def get_all_operations(self) -> list[OperationData]:
        """Get all operations."""
        return self._cache.all_operations

    def get_change_events(self) -> list[CachedChangeEvent]:
        """Get all change events."""
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
        """Get the number of cached role pages."""
        return len(self._role_pages)

    # ─────────────────────────────────────────────────────────────────────────
    # Misc key-value cache (for dynamic caches like roles_allowing_op:*)
    # ─────────────────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any:
        """Get a value from misc cache."""
        result = self._misc_cache.get(key)
        track_cache_hit("misc", result is not None, key)
        return result

    def set(self, key: str, value: Any) -> None:
        """Set a value in misc cache."""
        self._misc_cache[key] = value

    # ─────────────────────────────────────────────────────────────────────────
    # Build cache from raw data
    # ─────────────────────────────────────────────────────────────────────────

    def build_from_operations(self, operations: list[OperationData]) -> None:
        """Build cache with new operations, preserving other data."""
        ops_by_name_lower, ops_by_prefix = build_indexes(operations)
        self._cache = replace(
            self._cache,
            all_operations=operations,
            ops_by_name_lower=ops_by_name_lower,
            ops_by_prefix=ops_by_prefix,
        )

    def build_from_roles(self, roles: list[CachedRole]) -> None:
        """Build cache with new roles, preserving other data."""
        roles_by_id = {r.role_id: r for r in roles}
        self._cache = replace(self._cache, roles_by_id=roles_by_id)
        logger.info("Built roles index: %d roles", len(roles_by_id))

    def set_change_events(self, events: list[CachedChangeEvent]) -> None:
        """Set change events. Used for testing."""
        self._cache = replace(self._cache, all_change_events=events)

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
            matching = [
                op
                for op in cache.ops_by_name_lower.values()
                if (
                    q_lower in op.name.lower()
                    or q_lower in (op.display_name or "").lower()
                    or q_lower in (op.description or "").lower()
                    or q_lower in (op.provider_display_name or "").lower()
                    or q_lower in (op.resource_type_display_name or "").lower()
                )
            ]

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

    def invalidate_all(self) -> None:
        """Reset to empty cache and delete disk cache. Used for testing."""
        self._cache = CacheData()
        self._role_pages.clear()
        self._misc_cache.clear()
        self._preloaded = False
        self._loaded_cache_mtime = None
        delete_cache_file()

    # ─────────────────────────────────────────────────────────────────────────
    # Disk persistence
    # ─────────────────────────────────────────────────────────────────────────

    def needs_reload_from_disk(self, skip_interval_check: bool = False) -> bool:
        """Check if disk cache was updated by worker.

        At startup, any stale cache file is deleted. So any cache file that
        exists must have been created by the worker during this session.

        Args:
            skip_interval_check: If True, skip the throttle interval check.
                Used for double-checking after acquiring lock.
        """
        now = time.time()
        if not skip_interval_check and now - self._last_cache_check < self._cache_check_interval:
            return False

        self._last_cache_check = now

        current_mtime = get_cache_file_mtime()

        # No cache file on disk
        if current_mtime is None:
            return False

        # Cache file exists but we haven't loaded any yet (worker created it)
        if self._loaded_cache_mtime is None:
            logger.info("New cache file detected from worker (mtime: %s)", current_mtime)
            return True

        # Cache file is newer than what we loaded
        if current_mtime > self._loaded_cache_mtime:
            logger.info(
                "Disk cache updated (mtime: %s > %s)",
                current_mtime,
                self._loaded_cache_mtime,
            )
            return True
        return False

    def reload_from_disk_if_needed(self) -> bool:
        """Reload cache from disk if updated by worker.

        Thread-safe: uses a lock to prevent concurrent reloads.

        The disk file contains the complete CacheData with all computed fields.
        No recomputation is needed - just load and swap.
        """
        if not self.needs_reload_from_disk():
            logger.debug("Cache Reload not needed from disk")
            return False

        # Acquire lock to prevent concurrent reloads
        if not self._reload_lock.acquire(blocking=False):
            logger.debug("Cache Reload already in progress, skipping")
            return False

        try:
            # Double-check after acquiring lock (skip interval check)
            if not self.needs_reload_from_disk(skip_interval_check=True):
                return False

            logger.info("Reloading cache from disk...")
            cached = load_cache_from_disk()
            if cached is None:
                logger.warning("Failed to load cache from disk")
                return False

            # Get current mtime BEFORE validation so we can update it even on failure
            # This prevents retry loops when a bad cache file exists
            current_mtime = get_cache_file_mtime()

            # Validate required data
            if not cached.roles_by_id or not cached.all_operations:
                logger.warning("Loaded cache incomplete, keeping current")
                # Update mtime to prevent retry loop on same bad file
                self._loaded_cache_mtime = current_mtime
                return False

            # Check version - if outdated, need fresh rebuild
            if cached.metadata.version != CACHE_VERSION:
                logger.warning(
                    f"Cache version mismatch: {cached.metadata.version} != {CACHE_VERSION}, "
                    "keeping current (worker will rebuild)"
                )
                # Update mtime to prevent retry loop
                self._loaded_cache_mtime = current_mtime
                return False

            # Disk file contains complete CacheData with all computed fields.
            # Just swap it in - no recomputation needed!
            self.swap(cached)

            self._loaded_cache_mtime = current_mtime
            self._preloaded = True

            logger.info(
                f"Cache reloaded: {len(cached.roles_by_id)} roles, "
                f"{len(cached.all_operations)} ops, "
                f"{len(cached.role_coverage)} role coverages (precomputed)"
            )
            return True
        finally:
            self._reload_lock.release()
