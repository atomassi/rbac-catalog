"""Role Recommendation Service - orchestrates role matching with focused methods."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from azurerbac.azure.models import OperationData, Permission, RoleDefinition
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching.models import (
    CacheStats,
    ClassifiedOperations,
    CoverageResult,
    ExpandedMissing,
    OperationSets,
    Plane,
    PlaneActions,
    PlaneContext,
    RoleCoverage,
    RoleEvaluationContext,
    RoleInfo,
    RoleNetPermissions,
)
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


def _get_default_cache() -> CacheData:
    """Get the default cache from the global singleton."""
    from azurerbac.cache import get_cache_service

    return get_cache_service().container.cache


class PlaneContextFactory:
    """Factory for creating PlaneContext objects - eliminates duplication."""

    __slots__ = ("control_wildcard_ops", "data_wildcard_ops", "op_sets")

    def __init__(
        self,
        op_sets: OperationSets,
        control_wildcard_ops: dict[str, set[str]],
        data_wildcard_ops: dict[str, set[str]],
    ) -> None:
        self.op_sets = op_sets
        self.control_wildcard_ops = control_wildcard_ops
        self.data_wildcard_ops = data_wildcard_ops

    def create(
        self,
        classified: ClassifiedOperations,
        cached_coverage: RoleCoverage | None,
    ) -> tuple[PlaneContext, PlaneContext]:
        """Create PlaneContext objects for both planes."""
        control = PlaneContext(
            plane=Plane.CONTROL,
            all_ops=self.op_sets.all_control,
            wildcards=classified.control_wildcards,
            wildcard_ops_map=self.control_wildcard_ops,
            cached_ops=cached_coverage.control if cached_coverage else None,
        )
        data = PlaneContext(
            plane=Plane.DATA,
            all_ops=self.op_sets.all_data,
            wildcards=classified.data_wildcards,
            wildcard_ops_map=self.data_wildcard_ops,
            cached_ops=cached_coverage.data if cached_coverage else None,
        )
        return control, data


class PermissionAggregator:
    """Aggregates permission lists from a role - used in multiple places."""

    __slots__ = ("actions", "data_actions", "not_actions", "not_data_actions")

    def __init__(self, permissions: list[Permission]) -> None:
        self.actions: list[str] = []
        self.not_actions: list[str] = []
        self.data_actions: list[str] = []
        self.not_data_actions: list[str] = []

        for perm in permissions:
            self.actions.extend(perm.actions)
            self.not_actions.extend(perm.not_actions)
            self.data_actions.extend(perm.data_actions)
            self.not_data_actions.extend(perm.not_data_actions)

    def for_plane(self, plane: Plane) -> PlaneActions:
        """Get actions and exclusions for the specified plane."""
        if plane == Plane.CONTROL:
            return PlaneActions(self.actions, self.not_actions)
        return PlaneActions(self.data_actions, self.not_data_actions)


class RoleRecommendationService:
    """Service for recommending least-privilege roles based on requested operations."""

    __slots__ = (
        "_cache",
        "_ops_folded_to_orig",
        "_plane_factory",
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
            caches: Optional cache data. If None, uses global singleton (lazy).
        """
        self.op_sets = OperationSets.from_operations(all_operations)
        self.requested_ops_data_flags = requested_ops_data_flags or {}
        self._cache = caches

        self._ops_folded_to_orig: dict[str, str] = {
            op.name.casefold(): op.name for op in all_operations
        }

        # Pre-computed wildcard matches (populated by compute_wildcard_matches)
        self.control_wildcard_ops: dict[str, set[str]] = {}
        self.data_wildcard_ops: dict[str, set[str]] = {}

        # Factory for creating plane contexts (eliminates duplication)
        self._plane_factory = PlaneContextFactory(
            self.op_sets, self.control_wildcard_ops, self.data_wildcard_ops
        )

    @property
    def _caches(self) -> CacheData:
        """Get the cache data (injected or global singleton)."""
        if self._cache is not None:
            return self._cache
        return _get_default_cache()

    def restore_original_casing(self, operations: set[str]) -> list[str]:
        """Restore original casing for operation names."""
        return [self._ops_folded_to_orig.get(op, op) for op in operations]

    def get_cache_stats(self) -> CacheStats:
        """Get current cache entry counts for logging.

        Returns:
            CacheStats with current entry counts.
        """
        caches = self._caches
        return CacheStats(
            pattern_match=len(caches.pattern_match),
            partial_coverage=len(caches.partial_coverage),
            role_coverage=len(caches.role_coverage),
            wildcard_count=len(caches.wildcard_count),
        )

    def _get_plane_contexts(
        self,
        classified: ClassifiedOperations,
        cached_coverage: RoleCoverage | None,
    ) -> tuple[PlaneContext, PlaneContext]:
        """Create PlaneContext objects for control and data planes."""
        return self._plane_factory.create(classified, cached_coverage)

    def classify_operations(
        self,
        requested_operations: list[str],
    ) -> ClassifiedOperations:
        """Classify requested operations by plane (control/data) and type (explicit/wildcard).

        Returns:
            ClassifiedOperations with operations in appropriate categories.
        """
        control: set[str] = set()
        data: set[str] = set()
        control_wildcards: set[str] = set()
        data_wildcards: set[str] = set()

        for op in requested_operations:
            self._classify_single_operation(op, control, data, control_wildcards, data_wildcards)

        return ClassifiedOperations(
            control=frozenset(control),
            data=frozenset(data),
            control_wildcards=frozenset(control_wildcards),
            data_wildcards=frozenset(data_wildcards),
        )

    def _classify_single_operation(
        self,
        op: str,
        control: set[str],
        data: set[str],
        control_wildcards: set[str],
        data_wildcards: set[str],
    ) -> None:
        """Classify a single operation into the appropriate bucket.

        Extracted for clarity and testability (SRP).
        Note: Operations are stored lowercase for case-insensitive matching
        with RoleCoverage.
        """
        is_wildcard = is_wildcard_pattern(op)
        op_lower = op.lower()

        # Case 1: Explicit data action flag provided
        if op in self.requested_ops_data_flags:
            is_data_action = self.requested_ops_data_flags[op]
            target_set = (
                (data_wildcards if is_wildcard else data)
                if is_data_action
                else (control_wildcards if is_wildcard else control)
            )
            target_set.add(op_lower if not is_wildcard else op)
            return

        # Case 2: Wildcard without explicit flag - check both planes
        if is_wildcard:
            self._classify_wildcard_to_planes(op, control_wildcards, data_wildcards)
            return

        # Case 3: Explicit operation - classify by lookup (op_sets is lowercase)
        if op_lower in self.op_sets.all_data:
            data.add(op_lower)
        else:
            control.add(op_lower)

    def _classify_wildcard_to_planes(
        self,
        op: str,
        control_wildcards: set[str],
        data_wildcards: set[str],
    ) -> None:
        """Classify a wildcard to control/data planes based on matching operations."""
        matches_control = bool(get_matching_operations(op, self.op_sets.all_control, Plane.CONTROL))
        matches_data = bool(get_matching_operations(op, self.op_sets.all_data, Plane.DATA))

        if matches_control:
            control_wildcards.add(op)
        if matches_data:
            data_wildcards.add(op)
        if not matches_control and not matches_data:
            control_wildcards.add(op)  # Default to control

    def compute_wildcard_matches(self, classified: ClassifiedOperations) -> int:
        """Pre-compute wildcard pattern matches and return total requested count.

        Args:
            classified: The classified operations.

        Returns:
            Total count of requested operations (expanding wildcards).
        """
        total_count = 0

        # Explicit operations: count those that exist
        total_count += len(classified.control & self.op_sets.all_control)
        total_count += len(classified.data & self.op_sets.all_data)

        # Control plane wildcards
        total_count += self._compute_plane_wildcard_matches(
            classified.control_wildcards,
            self.op_sets.all_control,
            Plane.CONTROL,
            self.control_wildcard_ops,
        )

        # Data plane wildcards
        total_count += self._compute_plane_wildcard_matches(
            classified.data_wildcards,
            self.op_sets.all_data,
            Plane.DATA,
            self.data_wildcard_ops,
        )

        return total_count

    def _compute_plane_wildcard_matches(
        self,
        wildcards: frozenset[str],
        all_ops: frozenset[str],
        plane: Plane,
        wildcard_ops_map: dict[str, set[str]],
    ) -> int:
        """Compute wildcard matches for a single plane (DRY extraction)."""
        count = 0
        for pattern in wildcards:
            ops = get_matching_operations(pattern, all_ops, plane)
            wildcard_ops_map[pattern] = ops
            count += len(ops)
        return count

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
                ctx.wildcard_partial_coverage[key] = CoverageResult(
                    covered=len(covered_ops),
                    total=len(pattern_ops),
                    uncovered=len(pattern_ops) - len(covered_ops),
                    uncovered_samples=[],
                )

    def evaluate_role_fast_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: RoleCoverage,
    ) -> None:
        """Evaluate role coverage using cached coverage data (fast path).

        Uses PlaneContext to eliminate control/data code duplication.
        """
        control_plane, data_plane = self._get_plane_contexts(classified, cached_coverage)

        # Check explicit operations
        ctx.matched_ops.update(classified.control & cached_coverage.control)
        ctx.matched_ops.update(classified.data & cached_coverage.data)

        # Check wildcard patterns for each plane
        self._evaluate_wildcards_fast(ctx, control_plane)
        self._evaluate_wildcards_fast(ctx, data_plane)

        # Check for conditions
        ctx.has_conditions = any(p.has_condition for p in ctx.permissions)

    def evaluate_role_slow_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: RoleCoverage | None,
    ) -> None:
        """Evaluate role coverage by iterating permissions (slow path).

        Used when full cache is not available. Uses PlaneContext to avoid
        duplicating control/data plane logic.
        """
        control_plane, data_plane = self._get_plane_contexts(classified, cached_coverage)

        for perm in ctx.permissions:
            self._evaluate_permission_both_planes(ctx, perm, classified, control_plane, data_plane)

    def _evaluate_permission_both_planes(
        self,
        ctx: RoleEvaluationContext,
        perm: Permission,
        classified: ClassifiedOperations,
        control_plane: PlaneContext,
        data_plane: PlaneContext,
    ) -> None:
        """Evaluate a permission's coverage for both planes."""
        has_condition = perm.has_condition

        # Control plane
        self._evaluate_permission_for_plane(
            ctx=ctx,
            plane=control_plane,
            explicit_ops=classified.control,
            actions=perm.actions,
            not_actions=perm.not_actions,
            has_condition=has_condition,
        )

        # Data plane
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

        Uses PlaneContext to access plane-specific data, reducing parameter count.
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
        if self._has_partial_coverage(plane, pattern, actions, not_actions):
            ctx.wildcard_partial_coverage[key] = CoverageResult(
                covered=1, total=0, uncovered=0, uncovered_samples=[]
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

        return has_any_wildcard_coverage(pattern, actions, not_actions, plane.all_ops, plane.plane)

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
        planes_to_check = []
        if op in classified.control_wildcards:
            planes_to_check.append(Plane.CONTROL)
        if op in classified.data_wildcards:
            planes_to_check.append(Plane.DATA)

        for plane in planes_to_check:
            key = plane.make_key(op)
            if key not in ctx.fully_covered_wildcards:
                missing.add(op)
                return  # Only need to add once

    def calculate_matched_ops_count(
        self,
        ctx: RoleEvaluationContext,
        cached_coverage: RoleCoverage | None,
    ) -> int:
        """Calculate the total count of matched operations (expanding wildcards)."""
        aggregator = PermissionAggregator(ctx.permissions)

        count = 0
        for op in ctx.matched_ops:
            if not is_wildcard_pattern(op):
                count += 1
                continue

            # Count coverage in both planes
            count += self._count_wildcard_coverage_both_planes(ctx, op, aggregator, cached_coverage)

        return count

    def _count_wildcard_coverage_both_planes(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        aggregator: PermissionAggregator,
        cached_coverage: RoleCoverage | None,
    ) -> int:
        """Count wildcard coverage for both planes."""
        count = 0

        # Control plane
        actions, not_actions = aggregator.for_plane(Plane.CONTROL)
        count += self._count_wildcard_coverage(
            ctx=ctx,
            op=op,
            plane=Plane.CONTROL,
            all_ops=self.op_sets.all_control,
            actions=actions,
            not_actions=not_actions,
            cached_ops=cached_coverage.control if cached_coverage else None,
        )

        # Data plane
        data_actions, not_data_actions = aggregator.for_plane(Plane.DATA)
        count += self._count_wildcard_coverage(
            ctx=ctx,
            op=op,
            plane=Plane.DATA,
            all_ops=self.op_sets.all_data,
            actions=data_actions,
            not_actions=not_data_actions,
            cached_ops=cached_coverage.data if cached_coverage else None,
        )

        return count

    def _count_wildcard_coverage(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        all_ops: frozenset[str],
        actions: list[str],
        not_actions: list[str],
        cached_ops: set[str] | None,
    ) -> int:
        """Count coverage for a single wildcard pattern in one plane.

        Handles both partial and full coverage cases, using cache when available.
        """
        key = plane.make_key(op)

        if key in ctx.wildcard_partial_coverage:
            if cached_ops is not None:
                pattern_ops = get_matching_operations(op, all_ops, plane, caches=self._caches)
                return len(cached_ops & pattern_ops)
            result = count_wildcard_partial_coverage(
                op, actions, not_actions, all_ops, plane, caches=self._caches
            )
            return result.covered

        if key in ctx.fully_covered_wildcards:
            return count_operations_matching_pattern(op, all_ops, plane)

        return 0

    def calculate_permissions_count(
        self,
        ctx: RoleEvaluationContext,
    ) -> RoleNetPermissions:
        """Calculate control and data plane permission counts."""
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
                Plane.CONTROL,
                caches=self._caches,
            )
            data_count += count_net_permissions(
                perm.data_actions,
                perm.not_data_actions,
                self.op_sets.all_data,
                Plane.DATA,
                caches=self._caches,
            )

        return RoleNetPermissions(control_count, data_count)

    def check_cache_staleness(self) -> bool:
        """Check if the role coverage cache is stale and invalidate if needed.

        Returns:
            True if cache was invalidated.
        """
        cached_control_count, cached_data_count = self._caches.cache_ops_count
        current_control_count = len(self.op_sets.all_control)
        current_data_count = len(self.op_sets.all_data)

        # Cache is stale if either plane count differs
        # The (cached_control_count > 0 or cached_data_count > 0) check ensures
        # we only invalidate when there's actually cached data to invalidate
        has_cached_data = cached_control_count > 0 or cached_data_count > 0
        counts_differ = (
            current_control_count != cached_control_count or current_data_count != cached_data_count
        )
        cache_is_stale = has_cached_data and counts_differ

        if cache_is_stale:
            logger.warning(
                "Role coverage cache is stale: control ops %d -> %d, data ops %d -> %d. "
                "Invalidating cache.",
                cached_control_count,
                current_control_count,
                cached_data_count,
                current_data_count,
            )
            self._caches.role_coverage.clear()
            self._caches.role_net_permissions.clear()
            self._caches.pattern_match.clear()
            self._caches.wildcard_count.clear()
            self._caches.partial_coverage.clear()
            return True

        logger.debug(
            "Cache valid: control=%d, data=%d, role_coverage=%d entries, pattern_match=%d entries",
            current_control_count,
            current_data_count,
            len(self._caches.role_coverage),
            len(self._caches.pattern_match),
        )
        return False

    def has_full_cache(self) -> bool:
        """Check if full role coverage cache is available."""
        return len(self._caches.role_coverage) > 0

    def get_cached_coverage(self, role_id: str) -> RoleCoverage | None:
        """Get cached coverage for a role, if available."""
        return self._caches.role_coverage.get(role_id)

    def expand_missing_operations(
        self,
        ctx: RoleEvaluationContext,
        missing_ops: set[str],
        classified: ClassifiedOperations,
    ) -> ExpandedMissing:
        """Expand missing operations to show individual uncovered operations.

        For wildcards, this expands to show which specific operations are not covered.
        Returns operations with original casing for display.
        """
        aggregator = PermissionAggregator(ctx.permissions)
        expanded: list[str] = []
        total_count = 0

        for op in missing_ops:
            if not is_wildcard_pattern(op):
                # Restore original casing for explicit operations
                expanded.append(self._ops_folded_to_orig.get(op, op))
                total_count += 1
                continue

            result = self._expand_wildcard_missing(ctx, op, classified, aggregator)
            if result.operations:
                # Restore original casing for expanded operations
                restored = [self._ops_folded_to_orig.get(op, op) for op in result.operations]
                self._extend_unique_sorted(expanded, restored)
                total_count += result.total
            else:
                expanded.append(op)
                total_count += 1

        return ExpandedMissing(expanded, total_count)

    def _expand_wildcard_missing(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        classified: ClassifiedOperations,
        aggregator: PermissionAggregator,
    ) -> ExpandedMissing:
        """Expand a wildcard pattern to its uncovered operations."""
        expanded: list[str] = []
        total = 0

        # Control plane
        if op in classified.control_wildcards:
            plane_actions = aggregator.for_plane(Plane.CONTROL)
            ctrl = self._get_plane_uncovered(
                ctx=ctx,
                op=op,
                plane=Plane.CONTROL,
                in_plane=True,
                all_ops=self.op_sets.all_control,
                actions=plane_actions.actions,
                not_actions=plane_actions.not_actions,
            )
            expanded.extend(ctrl.operations)
            total += ctrl.total

        # Data plane
        if op in classified.data_wildcards:
            plane_actions = aggregator.for_plane(Plane.DATA)
            data = self._get_plane_uncovered(
                ctx=ctx,
                op=op,
                plane=Plane.DATA,
                in_plane=True,
                all_ops=self.op_sets.all_data,
                actions=plane_actions.actions,
                not_actions=plane_actions.not_actions,
            )
            expanded.extend(data.operations)
            total += data.total

        return ExpandedMissing(expanded, total)

    def _get_plane_uncovered(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        in_plane: bool,
        all_ops: frozenset[str],
        actions: list[str],
        not_actions: list[str],
    ) -> ExpandedMissing:
        """Get uncovered operations for a wildcard in one plane."""
        key = plane.make_key(op)

        if key in ctx.wildcard_partial_coverage:
            result = count_wildcard_partial_coverage(
                op, actions, not_actions, all_ops, plane, caches=self._caches
            )
            return ExpandedMissing(list(result.uncovered_samples), result.uncovered)

        if in_plane and key not in ctx.fully_covered_wildcards:
            matching = get_matching_operations(op, all_ops, plane, caches=self._caches)
            return ExpandedMissing(sorted(matching), len(matching))

        return ExpandedMissing([], 0)

    @staticmethod
    def _extend_unique_sorted(
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

    def finalize_partial_coverage(self, ctx: RoleEvaluationContext) -> None:
        """Add partially covered wildcards to matched_ops for display."""
        for key, info in ctx.wildcard_partial_coverage.items():
            if info.covered > 0:
                # Extract pattern from key (e.g., "ctrl:pattern" -> "pattern")
                pattern = key.split(":", 1)[1] if ":" in key else key
                ctx.matched_ops.add(pattern)

    @staticmethod
    def is_builtin_role(role: RoleDefinition) -> bool:
        """Check if a role is a built-in role."""
        return role.is_builtin

    @staticmethod
    def extract_role_info(role: RoleDefinition) -> RoleInfo:
        """Extract role information from a RoleDefinition."""
        return RoleInfo(
            role_id=role.role_id,
            role_name=role.role_name,
            description=role.description,
            permissions=role.properties.permissions,
        )
