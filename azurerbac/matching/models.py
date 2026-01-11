"""Data models and value objects for role matching.

This module contains dataclasses that encapsulate related data that travels together,
eliminating data clumps and providing type-safe operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

from azurerbac.core.types import JsonDict

if TYPE_CHECKING:
    from azurerbac.azure.models import OperationData


# =============================================================================
# NamedTuples (lightweight, immutable return types)
# =============================================================================


class WildcardCoverageResult(NamedTuple):
    """Result of wildcard pattern coverage calculation."""

    covered: int
    total: int
    uncovered: int
    uncovered_samples: list[str]


# =============================================================================
# Frozen Dataclasses (value objects, immutable)
# =============================================================================


@dataclass(frozen=True, slots=True)
class ClassifiedOperations:
    """Classified operations separated by plane and pattern type.

    This value object groups operations that are always passed together,
    eliminating the data clump of (control, data, control_wildcards, data_wildcards).
    """

    control: frozenset[str] = field(default_factory=frozenset)
    data: frozenset[str] = field(default_factory=frozenset)
    control_wildcards: frozenset[str] = field(default_factory=frozenset)
    data_wildcards: frozenset[str] = field(default_factory=frozenset)

    @property
    def all_requested(self) -> frozenset[str]:
        """All requested operations as a single set."""
        return self.control | self.data | self.control_wildcards | self.data_wildcards

    def __len__(self) -> int:
        """Total count of all classified operations."""
        return (
            len(self.control)
            + len(self.data)
            + len(self.control_wildcards)
            + len(self.data_wildcards)
        )


@dataclass(frozen=True, slots=True)
class OperationSets:
    """Pre-computed operation sets for matching.

    Encapsulates all operation sets and their cache keys,
    eliminating repeated parameter passing.
    """

    all_control: frozenset[str]
    all_data: frozenset[str]
    control_cache_key: int
    data_cache_key: int

    @classmethod
    def from_operations(cls, operations: list[OperationData]) -> OperationSets:
        """Build from a list of OperationData models.

        Args:
            operations: List of OperationData objects.

        Returns:
            OperationSets with control and data plane operations separated.
        """
        control = frozenset(op.name for op in operations if not op.is_data_action)
        data = frozenset(op.name for op in operations if op.is_data_action)
        return cls(
            all_control=control,
            all_data=data,
            control_cache_key=len(control),
            data_cache_key=len(data) + 1_000_000,  # Offset to avoid collision
        )


@dataclass(frozen=True, slots=True)
class WildcardCoverage:
    """Coverage information for a wildcard pattern.

    Tracks how much of a wildcard pattern is covered by a role.
    """

    pattern: str
    plane: str  # "control" or "data"
    covered_count: int
    total_count: int
    uncovered_samples: tuple[str, ...] = field(default_factory=tuple)

    @property
    def missing_count(self) -> int:
        """Number of operations not covered."""
        return max(0, self.total_count - self.covered_count)


# =============================================================================
# Mutable Dataclasses (result objects)
# =============================================================================


@dataclass(slots=True)
class RoleMatch:
    """Represents a role that matches the requested permissions.

    This is the result object returned by recommend_roles, containing
    all information about how well a role matches the requested operations.
    """

    role_id: str
    role_name: str
    description: str
    matched_operations: list[str] = field(default_factory=list)
    missing_operations: list[str] = field(default_factory=list)
    total_permissions_granted: int = 0
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

    @property
    def is_full_match(self) -> bool:
        """Check if all requested operations are covered."""
        return len(self.missing_operations) == 0

    def to_dict(self) -> JsonDict:
        """Convert to dictionary for API response."""
        return {
            "role_id": self.role_id,
            "role_name": self.role_name,
            "matched_operations": self.matched_operations,
            "missing_operations": self.missing_operations,
            "missing_operations_expanded": self.missing_operations_expanded,
            "missing_operations_count": self.missing_operations_count,
            "has_partial_wildcard_match": self.has_partial_wildcard_match,
            "total_permissions": self.total_permissions_granted,
            "control_plane_permissions": self.control_plane_permissions,
            "data_plane_permissions": self.data_plane_permissions,
            "is_high_privilege": self.is_high_privilege,
            "has_conditions": self.has_conditions,
            "match_percentage": self.match_percentage,
            "is_full_match": self.is_full_match,
            "matched_operations_count": self.matched_operations_count,
            "requested_operations_count": self.requested_operations_count,
        }
