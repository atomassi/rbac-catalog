"""Role Recommendation Service - orchestrates role matching with focused methods."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, NamedTuple

from azurerbac.azure.models import OperationData, Permission, RoleDefinition
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching.models import ClassifiedOperations, OperationSets
from azurerbac.matching.role_matching import (
    check_operation_allowed,
    check_wildcard_operation_allowed,
    count_net_permissions,
    count_operations_matching_pattern,
    count_wildcard_partial_coverage,
    get_matching_operations,
    has_any_wildcard_coverage,
)

if TYPE_CHECKING:
    from azurerbac.cache.models import CacheData

logger = logging.getLogger(__name__)


class Plane(Enum):
    """Operation plane identifier - eliminates string literals."""

    CONTROL = "ctrl"
    DATA = "data"


class PartialCoverageInfo(NamedTuple):
    """Partial coverage tracking for wildcard patterns.

    Using NamedTuple instead of bare tuple improves:
    - Readability: Named fields vs positional indices
    - Type safety: IDE can verify field access
    - Self-documenting: No need for comments explaining tuple positions
    """

    covered: int
    total: int
    uncovered: int
    samples: list[str]


WildcardKey = str  # "ctrl:pattern" or "data:pattern"


@dataclass(slots=True)
class RoleEvaluationContext:
    """Context for evaluating a single role's coverage."""

    role_id: str
    role_name: str
    description: str
    permissions: list[Permission]
    matched_ops: set[str] = field(default_factory=set)
    wildcard_partial_coverage: dict[WildcardKey, PartialCoverageInfo] = field(default_factory=dict)
    fully_covered_wildcards: set[WildcardKey] = field(default_factory=set)
    has_conditions: bool = False


@dataclass(frozen=True, slots=True)
class PlaneContext:
    """Encapsulates plane-specific data for DRY evaluation."""

    plane: Plane
    all_ops: frozenset[str]
    cache_key: int
    wildcards: frozenset[str]
    wildcard_ops_map: dict[str, set[str]]
    cached_ops: set[str] | None

    @property
    def prefix(self) -> str:
        """Key prefix for wildcard tracking."""
        return self.plane.value

    def make_key(self, pattern: str) -> WildcardKey:
        """Create a wildcard key for this plane."""
        return f"{self.prefix}:{pattern}"


class RoleRecommendationService:
    """Service for recommending least-privilege roles based on requested operations.

    Design:
    - Uses PlaneContext to eliminate control/data plane code duplication
    - Methods have single responsibilities (classify, evaluate, calculate)
    - Caching concerns are isolated from evaluation logic
    """

    __slots__ = (
        "_caches_override",
        "control_wildcard_ops",
        "data_wildcard_ops",
        "op_sets",
        "requested_ops_data_flags",
    )

    def __init__(
        self,
        all_operations: list[OperationData],
        requested_ops_data_flags: dict[str, bool] | None = None,
        *,
        caches: CacheData | None = None,
    ) -> None:
        """Initialize the service with operation data.

        Args:
            all_operations: List of all Azure operations.
            requested_ops_data_flags: Mapping of operation names to is_data_action flags.
            caches: Optional cache data. If None, uses global singleton.
        """
        self.op_sets = OperationSets.from_operations(all_operations)
        self.requested_ops_data_flags = requested_ops_data_flags or {}
        self._caches_override = caches

        # Pre-computed wildcard matches (populated by compute_wildcard_matches)
        self.control_wildcard_ops: dict[str, set[str]] = {}
        self.data_wildcard_ops: dict[str, set[str]] = {}

    @property
    def _caches(self) -> CacheData:
        """Get the cache data (override or global singleton)."""
        if self._caches_override is not None:
            return self._caches_override
        from azurerbac.cache import get_cache_service

        return get_cache_service().container.cache

    # =========================================================================
    # Plane Context Factory
    # =========================================================================

    def _get_plane_contexts(
        self,
        classified: ClassifiedOperations,
        cached_coverage: tuple[set[str], set[str]] | None,
    ) -> tuple[PlaneContext, PlaneContext]:
        """Create PlaneContext objects for control and data planes."""
        control_ctx = PlaneContext(
            plane=Plane.CONTROL,
            all_ops=self.op_sets.all_control,
            cache_key=self.op_sets.control_cache_key,
            wildcards=classified.control_wildcards,
            wildcard_ops_map=self.control_wildcard_ops,
            cached_ops=cached_coverage[0] if cached_coverage else None,
        )
        data_ctx = PlaneContext(
            plane=Plane.DATA,
            all_ops=self.op_sets.all_data,
            cache_key=self.op_sets.data_cache_key,
            wildcards=classified.data_wildcards,
            wildcard_ops_map=self.data_wildcard_ops,
            cached_ops=cached_coverage[1] if cached_coverage else None,
        )
        return control_ctx, data_ctx

    # =========================================================================
    # Classification
    # =========================================================================

    def classify_operations(
        self,
        requested_operations: list[str],
    ) -> ClassifiedOperations:
        """Classify requested operations by plane (control/data) and type (explicit/wildcard).

        Returns:
            ClassifiedOperations with operations in appropriate categories
        """
        control: set[str] = set()
        data: set[str] = set()
        control_wildcards: set[str] = set()
        data_wildcards: set[str] = set()

        for op in requested_operations:
            is_wildcard = is_wildcard_pattern(op)
            has_explicit_flag = op in self.requested_ops_data_flags

            if has_explicit_flag:
                is_data_action = self.requested_ops_data_flags[op]
                if is_wildcard:
                    (data_wildcards if is_data_action else control_wildcards).add(op)
                else:
                    (data if is_data_action else control).add(op)
            elif is_wildcard:
                # Wildcards without explicit flags can match both planes
                matches_control = bool(
                    get_matching_operations(
                        op, self.op_sets.all_control, self.op_sets.control_cache_key
                    )
                )
                matches_data = bool(
                    get_matching_operations(op, self.op_sets.all_data, self.op_sets.data_cache_key)
                )

                if matches_control:
                    control_wildcards.add(op)
                if matches_data:
                    data_wildcards.add(op)
                if not matches_control and not matches_data:
                    control_wildcards.add(op)  # Default to control
            elif op in self.op_sets.all_data:
                data.add(op)
            else:
                control.add(op)

        return ClassifiedOperations(
            control=frozenset(control),
            data=frozenset(data),
            control_wildcards=frozenset(control_wildcards),
            data_wildcards=frozenset(data_wildcards),
        )

    def compute_wildcard_matches(self, classified: ClassifiedOperations) -> int:
        """Pre-compute wildcard pattern matches and return total requested count.

        Args:
            classified: The classified operations

        Returns:
            Total count of requested operations (expanding wildcards)
        """
        total_count = 0

        # Explicit operations: count those that exist
        total_count += len(classified.control & self.op_sets.all_control)
        total_count += len(classified.data & self.op_sets.all_data)

        # Control plane wildcards
        for pattern in classified.control_wildcards:
            ops = get_matching_operations(
                pattern, self.op_sets.all_control, self.op_sets.control_cache_key
            )
            self.control_wildcard_ops[pattern] = ops
            total_count += len(ops)

        # Data plane wildcards
        for pattern in classified.data_wildcards:
            ops = get_matching_operations(
                pattern, self.op_sets.all_data, self.op_sets.data_cache_key
            )
            self.data_wildcard_ops[pattern] = ops
            total_count += len(ops)

        return total_count

    def _evaluate_wildcards_fast(
        self,
        ctx: RoleEvaluationContext,
        plane: PlaneContext,
    ) -> None:
        """Evaluate wildcard patterns using cached role operations.

        Uses PlaneContext to handle plane-specific logic uniformly.
        """
        if plane.cached_ops is None:
            return

        for pattern in plane.wildcards:
            pattern_ops = plane.wildcard_ops_map.get(pattern, set())
            covered_ops = plane.cached_ops & pattern_ops

            if not covered_ops:
                continue

            ctx.matched_ops.add(pattern)
            key = plane.make_key(pattern)

            if len(covered_ops) == len(pattern_ops):
                ctx.fully_covered_wildcards.add(key)
            else:
                ctx.wildcard_partial_coverage[key] = PartialCoverageInfo(
                    covered=len(covered_ops),
                    total=len(pattern_ops),
                    uncovered=len(pattern_ops) - len(covered_ops),
                    samples=[],
                )

    def evaluate_role_fast_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: tuple[set[str], set[str]],
    ) -> None:
        """Evaluate role coverage using cached coverage data (fast path).

        Uses PlaneContext to eliminate control/data code duplication.
        """
        control_plane, data_plane = self._get_plane_contexts(classified, cached_coverage)

        # Check explicit operations
        ctx.matched_ops.update(classified.control & cached_coverage[0])
        ctx.matched_ops.update(classified.data & cached_coverage[1])

        # Check wildcard patterns for each plane
        self._evaluate_wildcards_fast(ctx, control_plane)
        self._evaluate_wildcards_fast(ctx, data_plane)

        # Check for conditions
        ctx.has_conditions = any(p.has_condition for p in ctx.permissions)

    def evaluate_role_slow_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: tuple[set[str], set[str]] | None,
    ) -> None:
        """Evaluate role coverage by iterating permissions (slow path).

        Used when full cache is not available. Uses PlaneContext to avoid
        duplicating control/data plane logic.
        """
        control_plane, data_plane = self._get_plane_contexts(classified, cached_coverage)

        for perm in ctx.permissions:
            has_condition = perm.has_condition

            # Control plane: check explicit ops and wildcards
            self._evaluate_permission_for_plane(
                ctx=ctx,
                plane=control_plane,
                explicit_ops=classified.control,
                actions=perm.actions,
                not_actions=perm.not_actions,
                has_condition=has_condition,
            )

            # Data plane: check explicit ops and wildcards
            self._evaluate_permission_for_plane(
                ctx=ctx,
                plane=data_plane,
                explicit_ops=classified.data,
                actions=perm.data_actions,
                not_actions=perm.not_data_actions,
                has_condition=has_condition,
            )

    def _evaluate_permission_for_plane(
        self,
        ctx: RoleEvaluationContext,
        plane: PlaneContext,
        explicit_ops: frozenset[str],
        actions: list[str],
        not_actions: list[str],
        has_condition: bool,
    ) -> None:
        """Evaluate a single permission's coverage for one plane.

        This method handles both explicit operations and wildcard patterns
        for a given plane, eliminating the control/data code duplication.
        """
        # Check explicit operations
        for op in explicit_ops:
            if check_operation_allowed(op, actions, not_actions):
                ctx.matched_ops.add(op)
                if has_condition:
                    ctx.has_conditions = True

        # Check wildcard patterns
        for pattern in plane.wildcards:
            self._check_wildcard_for_permission(
                ctx=ctx,
                plane=plane,
                pattern=pattern,
                actions=actions,
                not_actions=not_actions,
                has_condition=has_condition,
            )

    def _check_wildcard_for_permission(
        self,
        ctx: RoleEvaluationContext,
        plane: PlaneContext,
        pattern: str,
        actions: list[str],
        not_actions: list[str],
        has_condition: bool,
    ) -> None:
        """Check wildcard pattern coverage for a single permission.

        Uses PlaneContext to access plane-specific data, reducing parameter count
        from 10 to 6 and improving readability.
        """
        key = plane.make_key(pattern)

        # Check if pattern is fully covered by this permission
        if check_wildcard_operation_allowed(pattern, actions, not_actions):
            ctx.matched_ops.add(pattern)
            ctx.fully_covered_wildcards.add(key)
            if has_condition:
                ctx.has_conditions = True
            return

        # Skip if already tracked as partial
        if key in ctx.wildcard_partial_coverage:
            return

        # Check for partial coverage
        has_coverage = self._has_partial_coverage(plane, pattern, actions, not_actions)
        if has_coverage:
            ctx.wildcard_partial_coverage[key] = PartialCoverageInfo(
                covered=1, total=0, uncovered=0, samples=[]
            )
            ctx.matched_ops.add(pattern)
            if has_condition:
                ctx.has_conditions = True

    def _has_partial_coverage(
        self,
        plane: PlaneContext,
        pattern: str,
        actions: list[str],
        not_actions: list[str],
    ) -> bool:
        """Check if there's any partial coverage for a wildcard pattern.

        Encapsulates the cache-aware partial coverage check logic.
        """
        if plane.cached_ops is not None:
            pattern_ops = plane.wildcard_ops_map.get(pattern, set())
            return bool(plane.cached_ops & pattern_ops)

        return has_any_wildcard_coverage(
            pattern, actions, not_actions, plane.all_ops, plane.cache_key
        )

    def calculate_missing_ops(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
    ) -> set[str]:
        """Calculate which requested operations are not covered by the role."""
        missing: set[str] = set()

        for op in classified.all_requested:
            if op not in ctx.matched_ops:
                missing.add(op)
            elif is_wildcard_pattern(op):
                self._check_wildcard_missing(ctx, op, classified, missing)

        return missing

    def _check_wildcard_missing(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        classified: ClassifiedOperations,
        missing: set[str],
    ) -> None:
        """Check if a wildcard pattern has missing operations in either plane."""
        if op in classified.control_wildcards:
            key = f"{Plane.CONTROL.value}:{op}"
            if key not in ctx.fully_covered_wildcards:
                missing.add(op)

        if op in classified.data_wildcards:
            key = f"{Plane.DATA.value}:{op}"
            if key not in ctx.fully_covered_wildcards:
                missing.add(op)

    def calculate_matched_ops_count(
        self,
        ctx: RoleEvaluationContext,
        cached_coverage: tuple[set[str], set[str]] | None,
    ) -> int:
        """Calculate the total count of matched operations (expanding wildcards)."""
        actions, not_actions, data_actions, not_data_actions = self._aggregate_permissions(
            ctx.permissions
        )

        count = 0
        for op in ctx.matched_ops:
            if not is_wildcard_pattern(op):
                count += 1
                continue

            # Count coverage in control plane
            count += self._count_wildcard_coverage(
                ctx=ctx,
                op=op,
                plane=Plane.CONTROL,
                all_ops=self.op_sets.all_control,
                cache_key=self.op_sets.control_cache_key,
                actions=actions,
                not_actions=not_actions,
                cached_ops=cached_coverage[0] if cached_coverage else None,
            )

            # Count coverage in data plane
            count += self._count_wildcard_coverage(
                ctx=ctx,
                op=op,
                plane=Plane.DATA,
                all_ops=self.op_sets.all_data,
                cache_key=self.op_sets.data_cache_key,
                actions=data_actions,
                not_actions=not_data_actions,
                cached_ops=cached_coverage[1] if cached_coverage else None,
            )

        return count

    def _count_wildcard_coverage(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        all_ops: frozenset[str],
        cache_key: int,
        actions: list[str],
        not_actions: list[str],
        cached_ops: set[str] | None,
    ) -> int:
        """Count coverage for a single wildcard pattern in one plane.

        Handles both partial and full coverage cases, using cache when available.
        """
        key = f"{plane.value}:{op}"

        if key in ctx.wildcard_partial_coverage:
            if cached_ops is not None:
                pattern_ops = get_matching_operations(op, all_ops, cache_key, caches=self._caches)
                return len(cached_ops & pattern_ops)
            result = count_wildcard_partial_coverage(
                op, actions, not_actions, all_ops, cache_key, caches=self._caches
            )
            return result.covered

        if key in ctx.fully_covered_wildcards:
            return count_operations_matching_pattern(op, all_ops, cache_key)

        return 0

    def _aggregate_permissions(
        self, permissions: list[Permission]
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Aggregate all action lists from role permissions.

        Returns (actions, not_actions, data_actions, not_data_actions).
        """
        actions: list[str] = []
        not_actions: list[str] = []
        data_actions: list[str] = []
        not_data_actions: list[str] = []

        for perm in permissions:
            actions.extend(perm.actions)
            not_actions.extend(perm.not_actions)
            data_actions.extend(perm.data_actions)
            not_data_actions.extend(perm.not_data_actions)

        return actions, not_actions, data_actions, not_data_actions

    def calculate_permissions_count(
        self,
        ctx: RoleEvaluationContext,
    ) -> tuple[int, int]:
        """Calculate control and data plane permission counts.

        Returns:
            Tuple of (control_plane_count, data_plane_count)
        """
        cached_perms = self._caches.role_net_permissions.get(ctx.role_id)
        if cached_perms:
            return cached_perms

        control_count = 0
        data_count = 0

        for perm in ctx.permissions:
            control_count += count_net_permissions(
                perm.actions,
                perm.not_actions,
                self.op_sets.all_control,
                self.op_sets.control_cache_key,
                caches=self._caches,
            )
            data_count += count_net_permissions(
                perm.data_actions,
                perm.not_data_actions,
                self.op_sets.all_data,
                self.op_sets.data_cache_key,
                caches=self._caches,
            )

        return control_count, data_count

    def check_cache_staleness(self) -> bool:
        """Check if the role coverage cache is stale and invalidate if needed.

        Returns:
            True if cache was invalidated
        """
        cached_control_count = self._caches.cache_ops_count[0]
        cached_data_count = self._caches.cache_ops_count[1]

        cache_is_stale = cached_control_count > 0 and (
            len(self.op_sets.all_control) != cached_control_count
            or len(self.op_sets.all_data) != cached_data_count
        )

        if cache_is_stale:
            logger.warning(
                f"Role coverage cache is stale: control ops {cached_control_count} "
                f"-> {len(self.op_sets.all_control)}, data ops {cached_data_count} "
                f"-> {len(self.op_sets.all_data)}. Invalidating cache."
            )
            self._caches.role_coverage.clear()
            self._caches.role_net_permissions.clear()
            return True

        return False

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def expand_missing_operations(
        self,
        ctx: RoleEvaluationContext,
        missing_ops: set[str],
        classified: ClassifiedOperations,
    ) -> tuple[list[str], int]:
        """Expand missing operations to show individual uncovered operations.

        For wildcards, this expands to show which specific operations are not covered.

        Args:
            ctx: Role evaluation context
            missing_ops: Set of missing operation patterns
            classified: Classified operations

        Returns:
            Tuple of (expanded operations list, total missing count)
        """
        actions, not_actions, data_actions, not_data_actions = self._aggregate_permissions(
            ctx.permissions
        )

        expanded: list[str] = []
        total_count = 0

        for op in missing_ops:
            if not is_wildcard_pattern(op):
                expanded.append(op)
                total_count += 1
                continue

            # Expand wildcard to individual missing operations
            ops, count = self._expand_wildcard_missing(
                ctx=ctx,
                op=op,
                classified=classified,
                actions=actions,
                not_actions=not_actions,
                data_actions=data_actions,
                not_data_actions=not_data_actions,
            )

            if ops:
                self._extend_unique_sorted(expanded, ops)
                total_count += count
            else:
                expanded.append(op)
                total_count += 1

        return expanded, total_count

    def _expand_wildcard_missing(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        classified: ClassifiedOperations,
        actions: list[str],
        not_actions: list[str],
        data_actions: list[str],
        not_data_actions: list[str],
    ) -> tuple[list[str], int]:
        """Expand a wildcard pattern to its uncovered operations."""
        expanded: list[str] = []
        total = 0

        # Control plane
        ctrl_ops, ctrl_count = self._get_plane_uncovered(
            ctx=ctx,
            op=op,
            plane=Plane.CONTROL,
            in_plane=op in classified.control_wildcards,
            all_ops=self.op_sets.all_control,
            cache_key=self.op_sets.control_cache_key,
            actions=actions,
            not_actions=not_actions,
        )
        expanded.extend(ctrl_ops)
        total += ctrl_count

        # Data plane
        data_ops, data_count = self._get_plane_uncovered(
            ctx=ctx,
            op=op,
            plane=Plane.DATA,
            in_plane=op in classified.data_wildcards,
            all_ops=self.op_sets.all_data,
            cache_key=self.op_sets.data_cache_key,
            actions=data_actions,
            not_actions=not_data_actions,
        )
        expanded.extend(data_ops)
        total += data_count

        return expanded, total

    def _get_plane_uncovered(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        in_plane: bool,
        all_ops: frozenset[str],
        cache_key: int,
        actions: list[str],
        not_actions: list[str],
    ) -> tuple[list[str], int]:
        """Get uncovered operations for a wildcard in one plane."""
        key = f"{plane.value}:{op}"

        if key in ctx.wildcard_partial_coverage:
            result = count_wildcard_partial_coverage(
                op, actions, not_actions, all_ops, cache_key, caches=self._caches
            )
            return list(result.uncovered_samples), result.uncovered

        if in_plane and key not in ctx.fully_covered_wildcards:
            matching = get_matching_operations(op, all_ops, cache_key, caches=self._caches)
            return sorted(matching), len(matching)

        return [], 0

    # =========================================================================
    # Cache and Utility Methods
    # =========================================================================

    def _extend_unique_sorted(
        self,
        dst: list[str],
        values: set[str] | list[str],
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> None:
        """Extend list with unique sorted values up to limit."""
        existing = set(dst)
        for v in sorted(values)[:limit]:
            if v not in existing:
                dst.append(v)
                existing.add(v)

    def has_full_cache(self) -> bool:
        """Check if full role coverage cache is available."""
        return len(self._caches.role_coverage) > 0

    def get_cached_coverage(self, role_id: str) -> tuple[set[str], set[str]] | None:
        """Get cached coverage for a role, if available."""
        return self._caches.role_coverage.get(role_id)

    def finalize_partial_coverage(self, ctx: RoleEvaluationContext) -> None:
        """Add partially covered wildcards to matched_ops for display."""
        for key, info in ctx.wildcard_partial_coverage.items():
            if info.covered > 0:
                pattern = key.split(":", 1)[1] if ":" in key else key
                ctx.matched_ops.add(pattern)

    def is_builtin_role(self, role: RoleDefinition) -> bool:
        """Check if a role is a built-in role."""
        return role.is_builtin

    def extract_role_info(self, role: RoleDefinition) -> tuple[str, str, str, list[Permission]]:
        """Extract role information from a RoleDefinition."""
        return (
            role.role_id,
            role.role_name,
            role.description,
            role.properties.permissions,
        )
