"""Web service layer models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from azurerbac.core.diffing import RoleDiff
from azurerbac.core.enums import SortOrder
from azurerbac.core.patterns import (
    expand_patterns_to_operations,
    is_wildcard_pattern,
    matches_pattern,
)
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


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Pagination parameters."""

    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class RawPermissions:
    """Raw permission patterns from a role with effective computation.

    Stores original permission blocks to correctly compute effective permissions
    using Azure RBAC semantics: union of (actions - notActions) per block.
    """

    __slots__ = ("_permissions",)

    def __init__(self, permissions: list[Permission]) -> None:
        self._permissions = permissions

    @classmethod
    def from_permissions(cls, permissions: list[Permission]) -> RawPermissions:
        """Create from a list of Permission objects."""
        return cls(permissions)

    @property
    def actions(self) -> list[str]:
        """Get all actions across all permission blocks (for display)."""
        return [a for p in self._permissions for a in p.actions]

    @property
    def not_actions(self) -> list[str]:
        """Get all notActions across all permission blocks (for display)."""
        return [a for p in self._permissions for a in p.not_actions]

    @property
    def data_actions(self) -> list[str]:
        """Get all dataActions across all permission blocks (for display)."""
        return [a for p in self._permissions for a in p.data_actions]

    @property
    def not_data_actions(self) -> list[str]:
        """Get all notDataActions across all permission blocks (for display)."""
        return [a for p in self._permissions for a in p.not_data_actions]

    @property
    def all_patterns(self) -> list[str]:
        """Get all patterns (actions + not_actions + data + not_data)."""
        return self.actions + self.not_actions + self.data_actions + self.not_data_actions

    @property
    def has_wildcards(self) -> bool:
        """Check if any patterns contain wildcards."""
        return any(is_wildcard_pattern(p) or p == "*" for p in self.all_patterns)

    @property
    def has_defined_permissions(self) -> bool:
        """Check if role has any defined permissions (actions or data_actions)."""
        return bool(self.actions or self.data_actions)

    def compute_effective(self, control_ops: set[str], data_ops: set[str]) -> RoleCoverage:
        """Compute effective permissions using correct Azure RBAC semantics.

        Azure RBAC computes effective permissions as:
            union of (block.actions - block.notActions) for each permission block

        NOT as: (union of all actions) - (union of all notActions)

        This matters when one block excludes an action that another block grants.
        """
        control_effective: set[str] = set()
        data_effective: set[str] = set()

        for perm in self._permissions:
            # Control plane: actions - notActions for this block
            block_control = expand_patterns_to_operations(perm.actions, control_ops)
            block_control -= expand_patterns_to_operations(perm.not_actions, control_ops)
            control_effective |= block_control

            # Data plane: dataActions - notDataActions for this block
            block_data = expand_patterns_to_operations(perm.data_actions, data_ops)
            block_data -= expand_patterns_to_operations(perm.not_data_actions, data_ops)
            data_effective |= block_data

        return RoleCoverage(control_effective, data_effective)

    def _build_permission_blocks(self) -> list[PermissionBlockView]:
        """Build permission block views for UI display."""
        return [
            PermissionBlockView(
                block_number=i + 1,
                actions=perm.actions,
                not_actions=perm.not_actions,
                data_actions=perm.data_actions,
                not_data_actions=perm.not_data_actions,
                has_condition=perm.has_condition,
                condition_text=perm.condition if perm.has_condition else None,
            )
            for i, perm in enumerate(self._permissions)
        ]

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
            raw_data_actions=self.data_actions,
            permission_blocks=self._build_permission_blocks(),
        )


class RolePermissionAnalyzer:
    """Analyzes role permissions using cache-backed operation data."""

    __slots__ = ("_cache", "_raw_permissions", "_role")

    def __init__(
        self,
        role: RoleDefinition,
        *,
        cache: CacheService | None = None,
    ) -> None:
        """Initialize the analyzer."""
        from azurerbac.cache import get_cache_service

        self._role = role
        self._cache = cache if cache is not None else get_cache_service()
        self._raw_permissions = RawPermissions.from_permissions(role.properties.permissions)

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
    def count_pages(total_items: int, page_size: int) -> int:
        """Compute total page count."""
        return max(1, (total_items + page_size - 1) // page_size)

    @staticmethod
    def compute(total_items: int, page: int, page_size: int) -> PaginationInfo:
        """Compute pagination values."""
        tp = PaginationInfo.count_pages(total_items, page_size)
        clamped_page = min(page, tp)
        start_idx = (clamped_page - 1) * page_size
        end_idx = start_idx + page_size
        return PaginationInfo(
            total_pages=tp,
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
class PermissionBlockView:
    """Single permission block for UI display."""

    block_number: int
    actions: list[str]
    not_actions: list[str]
    data_actions: list[str]
    not_data_actions: list[str]
    has_condition: bool
    condition_text: str | None

    @property
    def has_control_plane(self) -> bool:
        """Check if block has control plane permissions."""
        return bool(self.actions or self.not_actions)

    @property
    def has_data_plane(self) -> bool:
        """Check if block has data plane permissions."""
        return bool(self.data_actions or self.not_data_actions)


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
    raw_data_actions: list[str]
    permission_blocks: list[PermissionBlockView]


@dataclass(slots=True)
class EnrichedChangeEvent:
    """Change event with processed diff data."""

    scan_timestamp: datetime | None
    azure_updated_on: datetime | None
    event_type: str
    summary: str | None
    diff: RoleDiff | None
    diff_pretty: str
    role_json_pretty: str


@dataclass(frozen=True, slots=True)
class PermissionTimelinePoint:
    """A single point on the permission timeline chart.

    Represents both pattern counts (raw) and effective (expanded) permission
    counts at a specific role version.
    """

    date: str  # ISO date string for Chart.js
    version: int
    event_type: str
    actions: int  # Number of control-plane action patterns
    data_actions: int  # Number of data-plane action patterns
    total: int  # actions + data_actions (patterns)
    effective_actions: int  # Expanded control-plane operations
    effective_data_actions: int  # Expanded data-plane operations
    effective_total: int  # effective_actions + effective_data_actions
    label: str  # Human-readable label for tooltips

    def to_dict(self) -> dict[str, str | int]:
        """Serialize to a JSON-safe dict for Chart.js."""
        return {
            "date": self.date,
            "version": self.version,
            "event_type": self.event_type,
            "actions": self.actions,
            "data_actions": self.data_actions,
            "total": self.total,
            "effective_actions": self.effective_actions,
            "effective_data_actions": self.effective_data_actions,
            "effective_total": self.effective_total,
            "label": self.label,
        }


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


@dataclass(frozen=True, slots=True)
class RelatedRole:
    """A role related to another through shared operations."""

    role_id: str
    role_name: str
    similarity: float  # Composite score (ops overlap + scope + conditions), 0.0-1.0
    shared_count: int  # Number of shared operations
    total_count: int  # Total operations of the related role
    is_subset: bool = (
        False  # All ops in related are in current, scopes equal-or-narrower, same conditions
    )
    is_superset: bool = (
        False  # All ops in current are in related, scopes equal-or-broader, same conditions
    )


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


@dataclass(slots=True)
class DashboardSummary:
    """Dashboard summary data."""

    total_roles: int
    total_operations: int
    last_scan: datetime | None
    first_scan: datetime | None


@dataclass(slots=True)
class RoleDetailResult:
    """Role detail data from cache or database."""

    cached_role: CachedRole | None
    definition: RoleDefinition | None
    events: list[CachedChangeEvent]
    first_scan: datetime | None
