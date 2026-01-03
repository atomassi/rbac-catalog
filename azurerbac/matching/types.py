"""Type definitions and value objects for role matching.

This module contains dataclasses that encapsulate related data that travels together,
eliminating data clumps and providing type-safe operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    def from_operations(cls, operations: list[dict]) -> OperationSets:
        """Build from a list of operation dicts."""
        control = frozenset(op["name"] for op in operations if not op.get("is_data_action"))
        data = frozenset(op["name"] for op in operations if op.get("is_data_action"))
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
