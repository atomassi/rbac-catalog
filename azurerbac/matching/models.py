"""Role matching models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData
    from azurerbac.cache.models import CacheData

from azurerbac.azure.models import Permission


class Plane(Enum):
    """Operation plane identifier."""

    CONTROL = "ctrl"
    DATA = "data"

    def make_key(self, pattern: str) -> str:
        return f"{self.value}:{pattern}"


WildcardKey = str


class CoverageResult(NamedTuple):
    """Coverage statistics for wildcard patterns."""

    covered: int
    total: int
    uncovered: int
    uncovered_samples: list[str]


class RoleCoverage(NamedTuple):
    """Effective operations granted by a role after exclusions."""

    control: set[str]
    data: set[str]


class RoleNetPermissions(NamedTuple):
    """Permission counts for a role."""

    control_count: int
    data_count: int


class CacheOpsCount(NamedTuple):
    """Operation counts at cache build time (for staleness detection)."""

    control: int
    data: int


class CacheStats(NamedTuple):
    """Cache entry counts for logging/debugging."""

    pattern_match: int
    partial_coverage: int
    role_coverage: int
    wildcard_count: int


class PlaneActions(NamedTuple):
    """Actions and exclusions for a single plane."""

    actions: list[str]
    not_actions: list[str]


class ExpandedMissing(NamedTuple):
    """Expanded missing operations result."""

    operations: list[str]
    total: int


class RoleInfo(NamedTuple):
    """Basic role information."""

    role_id: str
    role_name: str
    description: str
    permissions: list[Permission]
    assignable_scope: str = "/"


class PatternCacheKey(NamedTuple):
    """Cache key for pattern matching."""

    pattern: str
    plane: Plane


class PartialCoverageCacheKey(NamedTuple):
    """Cache key for partial coverage lookups."""

    pattern: str
    plane: Plane
    actions: tuple[str, ...]
    not_actions: tuple[str, ...]

    @classmethod
    def build(
        cls,
        pattern: str,
        plane: Plane | None,
        actions: list[str],
        not_actions: list[str],
    ) -> PartialCoverageCacheKey | None:
        if plane is None:
            return None
        return cls(
            pattern=pattern,
            plane=plane,
            actions=tuple(sorted(actions)),
            not_actions=tuple(sorted(not_actions)),
        )


@dataclass(frozen=True, slots=True)
class ClassifiedOperations:
    """Operations separated by plane and pattern type."""

    control: frozenset[str] = field(default_factory=frozenset)
    data: frozenset[str] = field(default_factory=frozenset)
    control_wildcards: frozenset[str] = field(default_factory=frozenset)
    data_wildcards: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_requested(self) -> frozenset[str]:
        return self.control | self.data | self.control_wildcards | self.data_wildcards

    def __len__(self) -> int:
        return (
            len(self.control)
            + len(self.data)
            + len(self.control_wildcards)
            + len(self.data_wildcards)
        )


@dataclass(frozen=True, slots=True)
class OperationSets:
    """Pre-computed operation sets for matching (lowered)."""

    all_control: frozenset[str]
    all_data: frozenset[str]

    @classmethod
    def from_operations(cls, operations: list[OperationData]) -> OperationSets:
        """Build from raw operation list (slow - iterates all ops)."""
        control = frozenset(op.name.lower() for op in operations if not op.is_data_action)
        data = frozenset(op.name.lower() for op in operations if op.is_data_action)
        return cls(all_control=control, all_data=data)

    @classmethod
    def from_cache(cls, cache: CacheData) -> OperationSets:
        """Build from cached frozensets (fast - O(1))."""
        return cls(all_control=cache.control_ops_lowered, all_data=cache.data_ops_lowered)


@dataclass(frozen=True, slots=True)
class WildcardCoverage:
    """Coverage information for a wildcard pattern."""

    pattern: str
    plane: str
    covered_count: int
    total_count: int
    uncovered_samples: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_count(self) -> int:
        return max(0, self.total_count - self.covered_count)


@dataclass(slots=True)
class RoleEvaluationContext:
    """Context for evaluating a role's coverage."""

    role_id: str
    role_name: str
    description: str
    permissions: list[Permission]
    assignable_scope: str = "/"
    matched_ops: set[str] = field(default_factory=set)
    wildcard_partial_coverage: dict[WildcardKey, CoverageResult] = field(default_factory=dict)
    fully_covered_wildcards: set[WildcardKey] = field(default_factory=set)
    has_conditions: bool = False


@dataclass(frozen=True, slots=True)
class PlaneContext:
    """Plane-specific data for evaluation."""

    plane: Plane
    all_ops: frozenset[str]
    wildcards: frozenset[str]
    wildcard_ops_map: dict[str, set[str]]
    cached_ops: set[str] | None

    @property
    def prefix(self) -> str:
        return self.plane.value

    def make_key(self, pattern: str) -> WildcardKey:
        return self.plane.make_key(pattern)


@dataclass(slots=True)
class RoleMatch:
    """Role matching result."""

    role_id: str
    role_name: str
    description: str
    matched_operations: list[str] = field(default_factory=list)
    missing_operations: list[str] = field(default_factory=list)
    total_permissions: int = 0
    control_plane_permissions: int = 0
    data_plane_permissions: int = 0
    is_high_privilege: bool = False
    match_percentage: float = 0.0
    has_conditions: bool = False
    matched_operations_count: int = 0
    requested_operations_count: int = 0
    missing_operations_expanded: list[str] = field(default_factory=list)
    missing_operations_count: int = 0
    has_partial_wildcard_match: bool = False
    assignable_scope: str = "/"

    @property
    def is_full_match(self) -> bool:
        return len(self.missing_operations) == 0

    def to_dict(self) -> JsonDict:
        result = asdict(self)
        result["is_full_match"] = self.is_full_match
        return result
