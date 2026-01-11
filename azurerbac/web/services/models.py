"""Typed models for web service layer.

These dataclasses replace untyped dicts returned by service functions,
providing better type safety and IDE support.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from azurerbac.core.enums import SortOrder
from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, Permission, RoleDefinition
    from azurerbac.cache import CacheContainer
    from azurerbac.cache.models import CachedChangeEvent, CachedRole

# Note: CacheContainer is used in RolePermissionAnalyzer._cache property return type


# =============================================================================
# Operation Search/Filter Types
# =============================================================================


class OperationSortField(StrEnum):
    """Valid sort fields for operation listings."""

    NAME = "name"
    PROVIDER = "provider"
    TYPE = "type"
    ROLES = "roles"


class DataActionFilter(StrEnum):
    """Filter for data vs control plane actions.

    Maps query string values to boolean filters:
    - "1" -> True (data plane only)
    - "0" -> False (control plane only)
    - None/other -> all actions
    """

    DATA = "1"
    CONTROL = "0"

    @classmethod
    def parse(cls, value: str | None) -> bool | None:
        """Parse a query string value to a boolean filter.

        Args:
            value: Query string value ("1", "0", or None)

        Returns:
            True for data actions, False for control actions, None for all.
        """
        if value == cls.DATA:
            return True
        if value == cls.CONTROL:
            return False
        return None


@dataclass(frozen=True, slots=True)
class OperationSearchParams:
    """Parameters for operation search and filtering.

    Encapsulates all filter/sort parameters that often travel together.
    """

    query: str | None = None
    is_data_action: bool | None = None  # None = all, True = data, False = control
    provider: str | None = None
    sort: str | OperationSortField = OperationSortField.NAME
    order: str | SortOrder = SortOrder.ASC


class RawPermissions:
    """Raw permission patterns extracted from a role definition.

    Provides methods to compute effective permissions and check for wildcards.
    """

    __slots__ = ("actions", "data_actions", "not_actions", "not_data_actions")

    def __init__(
        self,
        actions: list[str],
        not_actions: list[str],
        data_actions: list[str],
        not_data_actions: list[str],
    ) -> None:
        self.actions = actions
        self.not_actions = not_actions
        self.data_actions = data_actions
        self.not_data_actions = not_data_actions

    @classmethod
    def from_permissions(cls, permissions: list[Permission]) -> RawPermissions:
        """Extract raw permission patterns from role permissions."""
        actions: list[str] = []
        not_actions: list[str] = []
        data_actions: list[str] = []
        not_data_actions: list[str] = []
        for perm in permissions:
            actions.extend(perm.actions)
            not_actions.extend(perm.not_actions)
            data_actions.extend(perm.data_actions)
            not_data_actions.extend(perm.not_data_actions)
        return cls(actions, not_actions, data_actions, not_data_actions)

    @property
    def all_patterns(self) -> list[str]:
        """Get all patterns (actions + not_actions + data + not_data)."""
        return self.actions + self.not_actions + self.data_actions + self.not_data_actions

    @property
    def has_wildcards(self) -> bool:
        """Check if any patterns contain wildcards."""
        from azurerbac.core.patterns import is_wildcard_pattern

        return any(is_wildcard_pattern(p) or p == "*" for p in self.all_patterns)

    @property
    def has_defined_permissions(self) -> bool:
        """Check if role has any defined permissions (actions or data_actions)."""
        return bool(self.actions or self.data_actions)

    def compute_effective(
        self, control_ops: set[str], data_ops: set[str]
    ) -> tuple[set[str], set[str]]:
        """Compute effective permissions after applying exclusions."""
        from azurerbac.core.patterns import expand_patterns_to_operations

        control_granted = expand_patterns_to_operations(self.actions, control_ops)
        control_excluded = expand_patterns_to_operations(self.not_actions, control_ops)
        control_effective = control_granted - control_excluded

        data_granted = expand_patterns_to_operations(self.data_actions, data_ops)
        data_excluded = expand_patterns_to_operations(self.not_data_actions, data_ops)
        data_effective = data_granted - data_excluded

        return control_effective, data_effective

    def to_effective_permissions(
        self,
        control_effective: set[str],
        data_effective: set[str],
        *,
        has_conditions: bool,
    ) -> RoleEffectivePermissions:
        """Build RoleEffectivePermissions from computed effective sets.

        Args:
            control_effective: Set of effective control plane operation names.
            data_effective: Set of effective data plane operation names.
            has_conditions: Whether the role has ABAC conditions.

        Returns:
            RoleEffectivePermissions with all metadata populated.
        """
        has_resolved = bool(control_effective or data_effective)
        has_unresolved = self.has_defined_permissions and not has_resolved

        return RoleEffectivePermissions(
            control_plane_actions=sorted(control_effective),
            data_plane_actions=sorted(data_effective),
            control_plane_count=len(control_effective),
            data_plane_count=len(data_effective),
            has_conditions=has_conditions,
            has_wildcards=self.has_wildcards,
            has_unresolved_permissions=has_unresolved,
            raw_actions=self.actions,
            raw_not_actions=self.not_actions,
            raw_data_actions=self.data_actions,
            raw_not_data_actions=self.not_data_actions,
        )


class RolePermissionAnalyzer:
    """Analyzes a role's permissions using cache-backed operation data.

    Encapsulates the logic for computing effective permissions and
    finding pattern matches. Uses the global cache singleton by default,
    but accepts an optional cache parameter for testing.

    Example:
        analyzer = RolePermissionAnalyzer(role)
        effective = analyzer.get_effective_permissions(all_operations)
        match = analyzer.find_matching_pattern("Microsoft.Storage/read", is_data_action=False)

        # For testing with a mock cache:
        analyzer = RolePermissionAnalyzer(role, cache=mock_cache)
    """

    __slots__ = ("_cache_override", "_raw_permissions", "_role")

    def __init__(
        self,
        role: RoleDefinition,
        *,
        cache: CacheContainer | None = None,
    ) -> None:
        """Initialize the analyzer.

        Args:
            role: The RoleDefinition to analyze.
            cache: Optional cache container. If None, uses the global singleton.
        """
        self._role = role
        self._cache_override = cache
        self._raw_permissions = RawPermissions.from_permissions(role.properties.permissions)

    @property
    def _cache(self) -> CacheContainer:
        """Get the cache container (override or global singleton)."""
        if self._cache_override is not None:
            return self._cache_override
        from azurerbac.cache import get_cache_service

        return get_cache_service().container

    @property
    def role_id(self) -> str:
        """Get the role's unique identifier."""
        return self._role.name

    @property
    def permissions(self) -> list[Permission]:
        """Get the role's permission list."""
        return self._role.properties.permissions

    @property
    def raw(self) -> RawPermissions:
        """Get the extracted raw permission patterns."""
        return self._raw_permissions

    @property
    def has_conditions(self) -> bool:
        """Check if any permissions have ABAC conditions."""
        return any(perm.condition for perm in self.permissions)

    def get_cached_coverage(self) -> tuple[set[str], set[str]] | None:
        """Get pre-computed coverage from cache if available."""
        return self._cache.get_role_coverage(self.role_id)

    def compute_coverage(self, all_operations: list[OperationData]) -> tuple[set[str], set[str]]:
        """Compute coverage manually from operation list.

        Args:
            all_operations: List of all known Azure operations.

        Returns:
            Tuple of (control_effective, data_effective) operation sets.
        """
        all_control_ops = {op.name for op in all_operations if not op.is_data_action}
        all_data_ops = {op.name for op in all_operations if op.is_data_action}
        return self.raw.compute_effective(all_control_ops, all_data_ops)

    def get_effective_permissions(
        self, all_operations: list[OperationData]
    ) -> RoleEffectivePermissions:
        """Compute the effective permissions for this role.

        Uses cached coverage when available, falls back to manual computation.

        Args:
            all_operations: List of all known Azure operations.

        Returns:
            RoleEffectivePermissions with control/data plane actions and metadata.
        """
        # Try cache first
        if cached := self.get_cached_coverage():
            control_effective, data_effective = cached
        else:
            control_effective, data_effective = self.compute_coverage(all_operations)

        return self.raw.to_effective_permissions(
            control_effective,
            data_effective,
            has_conditions=self.has_conditions,
        )

    def find_matching_pattern(
        self, operation_name: str, *, is_data_action: bool
    ) -> PatternMatchResult:
        """Find the pattern that grants an operation and check for conditions.

        Args:
            operation_name: The operation name to match.
            is_data_action: Whether this is a data plane action.

        Returns:
            PatternMatchResult with matched pattern and condition info.
        """
        from azurerbac.core.patterns import matches_pattern

        operation_lower = operation_name.lower()

        for perm in self.permissions:
            actions = perm.data_actions if is_data_action else perm.actions
            for pattern in actions:
                if matches_pattern(operation_name, pattern):
                    condition = perm.condition or ""
                    has_condition = bool(condition and operation_lower in condition.lower())
                    return PatternMatchResult(
                        matched_pattern=pattern,
                        has_condition=has_condition,
                        condition_text=condition if has_condition else None,
                    )

        return PatternMatchResult(matched_pattern=None, has_condition=False, condition_text=None)


# =============================================================================
# Pagination Models
# =============================================================================


@dataclass(frozen=True, slots=True)
class PaginationInfo:
    """Computed pagination metadata.

    Holds derived values (total_pages, start/end indices) to avoid
    repeating the same calculations in multiple routes.
    """

    total_pages: int
    start_idx: int
    end_idx: int

    @staticmethod
    def compute(total_items: int, page: int, page_size: int) -> PaginationInfo:
        """Compute pagination values from item count and page parameters.

        Args:
            total_items: Total number of items before pagination.
            page: Current page number (1-based).
            page_size: Number of items per page.

        Returns:
            PaginationInfo with total_pages, start_idx, end_idx.
        """
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        # Clamp page to available pages
        clamped_page = min(page, total_pages)
        start_idx = (clamped_page - 1) * page_size
        end_idx = start_idx + page_size
        return PaginationInfo(
            total_pages=total_pages,
            start_idx=start_idx,
            end_idx=end_idx,
        )


@dataclass(frozen=True, slots=True)
class PaginatedResult[T]:
    """Generic paginated result container.

    Replaces tuple[list[T], int, int] returns with a typed dataclass.
    """

    items: list[T]
    total_count: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PatternMatchResult:
    """Result of finding a pattern that matches an operation.

    Used when checking which permission pattern grants an operation
    and whether conditions apply.
    """

    matched_pattern: str | None
    has_condition: bool
    condition_text: str | None


@dataclass(frozen=True, slots=True)
class ScanMetadata:
    """Scan timestamp metadata from cache or database.

    Contains first and last scan timestamps for display purposes.
    """

    last_scan: datetime | None
    first_scan: datetime | None


@dataclass(slots=True)
class RoleEffectivePermissions:
    """Effective permissions for a role after applying notActions/notDataActions.

    Contains control plane and data plane actions, counts, and metadata
    about wildcards and conditions.
    """

    control_plane_actions: list[str]
    data_plane_actions: list[str]
    control_plane_count: int
    data_plane_count: int
    has_conditions: bool
    has_wildcards: bool
    has_unresolved_permissions: bool
    raw_actions: list[str]
    raw_not_actions: list[str]
    raw_data_actions: list[str]
    raw_not_data_actions: list[str]

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "control_plane_actions": self.control_plane_actions,
            "data_plane_actions": self.data_plane_actions,
            "control_plane_count": self.control_plane_count,
            "data_plane_count": self.data_plane_count,
            "has_conditions": self.has_conditions,
            "has_wildcards": self.has_wildcards,
            "has_unresolved_permissions": self.has_unresolved_permissions,
            "raw_actions": self.raw_actions,
            "raw_not_actions": self.raw_not_actions,
            "raw_data_actions": self.raw_data_actions,
            "raw_not_data_actions": self.raw_not_data_actions,
        }


@dataclass(slots=True)
class EnrichedChangeEvent:
    """A role change event enriched with processed diff data.

    Used for displaying role history with formatted diff output.
    """

    scan_timestamp: datetime | None
    azure_updated_on: datetime | None
    event_type: str
    summary: str | None
    diff: JsonDict | None
    diff_pretty: str
    role_json_pretty: str

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "scan_timestamp": self.scan_timestamp,
            "azure_updated_on": self.azure_updated_on,
            "event_type": self.event_type,
            "summary": self.summary,
            "diff": self.diff,
            "diff_pretty": self.diff_pretty,
            "role_json_pretty": self.role_json_pretty,
        }


@dataclass(slots=True)
class RoleAllowingOperation:
    """A role that allows a specific operation.

    Contains role identification, the matched pattern, action counts,
    and condition information.
    """

    role_id: str
    role_name: str
    role_type: str
    matched_pattern: str
    actions_count: int
    data_actions_count: int
    has_condition: bool
    condition_text: str | None

    @classmethod
    def from_match(
        cls,
        role_id: str,
        role_name: str,
        role_type: str,
        control_count: int,
        data_count: int,
        match_result: PatternMatchResult,
    ) -> RoleAllowingOperation:
        """Create from role data and pattern match result.

        Args:
            role_id: The role's unique identifier.
            role_name: Display name of the role.
            role_type: Type of the role (e.g., "BuiltInRole").
            control_count: Number of control plane actions.
            data_count: Number of data plane actions.
            match_result: The pattern match result.

        Returns:
            RoleAllowingOperation instance.
        """
        return cls(
            role_id=role_id,
            role_name=role_name,
            role_type=role_type,
            matched_pattern=match_result.matched_pattern or "*",
            actions_count=control_count,
            data_actions_count=data_count,
            has_condition=match_result.has_condition,
            condition_text=match_result.condition_text,
        )

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_type": self.role_type,
            "matched_pattern": self.matched_pattern,
            "actions_count": self.actions_count,
            "data_actions_count": self.data_actions_count,
            "has_condition": self.has_condition,
            "condition_text": self.condition_text,
        }


@dataclass(slots=True)
class RoleWithCounts:
    """A role enriched with action counts from cache.

    Used for dashboard role listings where counts are needed.
    """

    role_id: str
    role_name: str
    role_type: str
    status: str
    updated_on: datetime | None
    actions_count: int
    data_actions_count: int

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "role_type": self.role_type,
            "status": self.status,
            "updated_on": self.updated_on,
            "actions_count": self.actions_count,
            "data_actions_count": self.data_actions_count,
        }


@dataclass(slots=True)
class DashboardSummary:
    """Summary data for dashboard pages.

    Contains total counts and scan timestamps.
    """

    total_roles: int
    total_operations: int
    last_scan: datetime | None
    first_scan: datetime | None

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return {
            "total_roles": self.total_roles,
            "total_operations": self.total_operations,
            "last_scan": self.last_scan,
            "first_scan": self.first_scan,
        }


@dataclass(slots=True)
class RoleDetailResult:
    """Result of fetching role detail data from cache or database.

    Replaces tuple return from get_role_from_cache_or_db for better type safety.
    """

    cached_role: CachedRole | None
    definition: RoleDefinition | None
    events: list[CachedChangeEvent]
    first_scan: datetime | None
