"""Web service layer models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from azurerbac.core.enums import SortOrder
from azurerbac.core.types import JsonDict
from azurerbac.matching.models import RoleCoverage

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData, Permission, RoleDefinition
    from azurerbac.cache import CacheService
    from azurerbac.cache.models import CachedChangeEvent, CachedRole


class OperationSortField(StrEnum):
    """Sort fields for operation listings."""

    NAME = "name"
    PROVIDER = "provider"
    TYPE = "type"
    ROLES = "roles"


class DataActionFilter(StrEnum):
    """Data vs control plane filter."""

    DATA = "1"
    CONTROL = "0"

    @classmethod
    def parse(cls, value: str | None) -> bool | None:
        """Parse query string to boolean filter."""
        if value == cls.DATA:
            return True
        if value == cls.CONTROL:
            return False
        return None


@dataclass(frozen=True, slots=True)
class OperationSearchParams:
    """Operation search parameters."""

    query: str | None = None
    is_data_action: bool | None = None
    provider: str | None = None
    sort: str | OperationSortField = OperationSortField.NAME
    order: str | SortOrder = SortOrder.ASC


class SortField(StrEnum):
    """Sort fields for role listings."""

    ACTIONS = "actions"
    DATA_ACTIONS = "data_actions"
    ID = "id"
    UPDATED = "updated"
    NAME = "name"

    @classmethod
    def from_string(cls, value: str) -> SortField:
        """Parse string to SortField."""
        try:
            return cls(value)
        except ValueError:
            return cls.NAME

    def requires_python_sort(self) -> bool:
        """True if requires in-memory sorting."""
        return self in {SortField.ACTIONS, SortField.DATA_ACTIONS}


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Pagination parameters."""

    page: int
    page_size: int
    sort: str | SortField = SortField.NAME
    order: str | SortOrder = SortOrder.ASC

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def is_descending(self) -> bool:
        return self.order == SortOrder.DESC

    @property
    def sort_field(self) -> SortField:
        return SortField.from_string(str(self.sort))


class RawPermissions:
    """Raw permission patterns from a role."""

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

    def compute_effective(self, control_ops: set[str], data_ops: set[str]) -> RoleCoverage:
        """Compute effective permissions after applying exclusions."""
        from azurerbac.core.patterns import expand_patterns_to_operations

        control_granted = expand_patterns_to_operations(self.actions, control_ops)
        control_excluded = expand_patterns_to_operations(self.not_actions, control_ops)
        control_effective = control_granted - control_excluded

        data_granted = expand_patterns_to_operations(self.data_actions, data_ops)
        data_excluded = expand_patterns_to_operations(self.not_data_actions, data_ops)
        data_effective = data_granted - data_excluded

        return RoleCoverage(control_effective, data_effective)

    def to_effective_permissions(
        self,
        control_effective: list[str],
        data_effective: list[str],
        *,
        has_conditions: bool,
    ) -> RoleEffectivePermissions:
        """Build RoleEffectivePermissions from computed lists."""
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
    """Analyzes role permissions using cache-backed operation data."""

    __slots__ = ("_cache_override", "_raw_permissions", "_role")

    def __init__(
        self,
        role: RoleDefinition,
        *,
        cache: CacheService | None = None,
    ) -> None:
        """Initialize the analyzer."""
        self._role = role
        self._cache_override = cache
        self._raw_permissions = RawPermissions.from_permissions(role.properties.permissions)

    @property
    def _cache(self) -> CacheService:
        """Get cache service."""
        if self._cache_override is not None:
            return self._cache_override
        from azurerbac.cache import get_cache_service

        return get_cache_service()

    @property
    def role_id(self) -> str:
        """Get role's unique identifier."""
        return self._role.name

    @property
    def permissions(self) -> list[Permission]:
        """Get role's permission list."""
        return self._role.properties.permissions

    @property
    def raw(self) -> RawPermissions:
        """Get extracted raw permission patterns."""
        return self._raw_permissions

    @property
    def has_conditions(self) -> bool:
        """Check if any permissions have conditions."""
        return any(perm.condition for perm in self.permissions)

    def get_cached_coverage(self) -> RoleCoverage | None:
        """Get pre-computed coverage from cache."""
        return self._cache.get_role_coverage(self.role_id)

    def compute_coverage(self, all_operations: list[OperationData]) -> RoleCoverage:
        """Compute coverage from operation list.

        Note: Operations are lowercased to match the cache behavior.
        restore_operation_casing() expects lowercased operation names.
        """
        all_control_ops = {op.name.lower() for op in all_operations if not op.is_data_action}
        all_data_ops = {op.name.lower() for op in all_operations if op.is_data_action}
        return self.raw.compute_effective(all_control_ops, all_data_ops)

    def get_effective_permissions(
        self, all_operations: list[OperationData]
    ) -> RoleEffectivePermissions:
        """Compute effective permissions for this role."""
        if cached := self.get_cached_coverage():
            control_effective, data_effective = cached
        else:
            control_effective, data_effective = self.compute_coverage(all_operations)

        return self.raw.to_effective_permissions(
            self._cache.restore_operation_casing(control_effective),
            self._cache.restore_operation_casing(data_effective),
            has_conditions=self.has_conditions,
        )

    def find_matching_pattern(
        self, operation_name: str, *, is_data_action: bool
    ) -> PatternMatchResult:
        """Find pattern granting an operation."""
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


@dataclass(frozen=True, slots=True)
class PaginationInfo:
    """Computed pagination metadata."""

    total_pages: int
    start_idx: int
    end_idx: int

    @staticmethod
    def compute(total_items: int, page: int, page_size: int) -> PaginationInfo:
        """Compute pagination values."""
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
    """Generic paginated result container."""

    items: list[T]
    total_count: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PatternMatchResult:
    """Pattern match result."""

    matched_pattern: str | None
    has_condition: bool
    condition_text: str | None


@dataclass(frozen=True, slots=True)
class ScanMetadata:
    """Scan timestamp metadata."""

    last_scan: datetime | None
    first_scan: datetime | None


@dataclass(slots=True)
class RoleEffectivePermissions:
    """Effective permissions after applying notActions."""

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
        return asdict(self)


@dataclass(slots=True)
class EnrichedChangeEvent:
    """Change event with processed diff data."""

    scan_timestamp: datetime | None
    azure_updated_on: datetime | None
    event_type: str
    summary: str | None
    diff: JsonDict | None
    diff_pretty: str
    role_json_pretty: str

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return asdict(self)


@dataclass(slots=True)
class RoleAllowingOperation:
    """Role that allows a specific operation."""

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
        """Create from role data and match result."""
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
        return asdict(self)


@dataclass(slots=True)
class RoleWithCounts:
    """Role with action counts."""

    role_id: str
    role_name: str
    role_type: str
    status: str
    updated_on: datetime | None
    actions_count: int
    data_actions_count: int

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return asdict(self)


@dataclass(slots=True)
class DashboardSummary:
    """Dashboard summary data."""

    total_roles: int
    total_operations: int
    last_scan: datetime | None
    first_scan: datetime | None

    def to_dict(self) -> JsonDict:
        """Convert to dict for template rendering."""
        return asdict(self)


@dataclass(slots=True)
class RoleDetailResult:
    """Role detail data from cache or database."""

    cached_role: CachedRole | None
    definition: RoleDefinition | None
    events: list[CachedChangeEvent]
    first_scan: datetime | None
