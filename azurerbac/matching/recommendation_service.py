"""Role Recommendation Service - orchestrates role matching with focused methods."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from azurerbac.azure.models import RoleDefinition
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching.models import (
    CacheStats,
    ClassifiedOperations,
    CoverageResult,
    ExpandedMissing,
    OperationSets,
    Plane,
    PlaneContext,
    RoleCoverage,
    RoleEvaluationContext,
    RoleInfo,
    RoleNetPermissions,
)
from azurerbac.matching.role_matching import (
    count_net_permissions,
    count_operations_matching_pattern,
    get_matching_operations,
)

if TYPE_CHECKING:
    from azurerbac.cache.models import CacheData

logger = logging.getLogger(__name__)


def _get_default_cache() -> CacheData:
    """Get the default cache from the global singleton."""
    from azurerbac.cache import get_cache_service

    return get_cache_service().cache


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


class RoleRecommendationService:
    """Service for recommending least-privilege roles based on requested operations."""

    __slots__ = (
        "_cache",
        "_ops_lowered_to_orig",
        "_plane_factory",
        "control_wildcard_ops",
        "data_wildcard_ops",
        "op_sets",
        "requested_ops_data_flags",
    )

    def __init__(
        self,
        requested_ops_data_flags: dict[str, bool] | None = None,
        *,
        cache: CacheData | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            requested_ops_data_flags: Mapping of operation names to is_data_action flags.
            cache: Optional cache data. If None, uses global singleton.
        """
        # Capture cache eagerly to ensure op_sets and _cache are always in sync.
        self._cache = cache if cache is not None else _get_default_cache()

        # Use cached frozensets (O(1)) instead of rebuilding from operations (O(n))
        self.op_sets = OperationSets.from_cache(self._cache)
        self.requested_ops_data_flags = requested_ops_data_flags or {}

        # Use cached mapping instead of rebuilding
        self._ops_lowered_to_orig = self._cache.ops_lowered_to_orig

        # Pre-computed wildcard matches (populated by compute_wildcard_matches)
        self.control_wildcard_ops: dict[str, set[str]] = {}
        self.data_wildcard_ops: dict[str, set[str]] = {}

        # Factory for creating plane contexts (eliminates duplication)
        self._plane_factory = PlaneContextFactory(
            self.op_sets, self.control_wildcard_ops, self.data_wildcard_ops
        )

    @property
    def _caches(self) -> CacheData:
        """Get the cache data (captured at construction time)."""
        return self._cache

    def restore_original_casing(self, operations: set[str]) -> list[str]:
        """Restore original casing for operation names."""
        return [self._ops_lowered_to_orig.get(op, op) for op in operations]

    def get_all_roles(self) -> list[RoleDefinition]:
        """Get all active roles from the cache."""
        return self._cache.get_role_definitions()

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
        Note: Operations are stored lowered for case-insensitive matching
        with RoleCoverage.
        """
        is_wildcard = is_wildcard_pattern(op)
        op_lowered = op.lower()

        # Case 1: Explicit data action flag provided
        if op in self.requested_ops_data_flags:
            is_data_action = self.requested_ops_data_flags[op]
            target_set = (
                (data_wildcards if is_wildcard else data)
                if is_data_action
                else (control_wildcards if is_wildcard else control)
            )
            target_set.add(op_lowered if not is_wildcard else op)
            return

        # Case 2: Wildcard without explicit flag - check both planes
        if is_wildcard:
            self._classify_wildcard_to_planes(op, control_wildcards, data_wildcards)
            return

        # Case 3: Explicit operation - classify by lookup (op_sets is lowered)
        if op_lowered in self.op_sets.all_data:
            data.add(op_lowered)
        else:
            control.add(op_lowered)

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
        cached_coverage: RoleCoverage,
    ) -> int:
        """Calculate the total count of matched operations (expanding wildcards)."""
        count = 0
        for op in ctx.matched_ops:
            if not is_wildcard_pattern(op):
                count += 1
                continue

            # Count coverage in both planes
            count += self._count_wildcard_coverage_both_planes(ctx, op, cached_coverage)

        return count

    def _count_wildcard_coverage_both_planes(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        cached_coverage: RoleCoverage,
    ) -> int:
        """Count wildcard coverage for both planes."""
        count = 0

        # Control plane
        count += self._count_wildcard_coverage(
            ctx=ctx,
            op=op,
            plane=Plane.CONTROL,
            all_ops=self.op_sets.all_control,
            cached_ops=cached_coverage.control,
        )

        # Data plane
        count += self._count_wildcard_coverage(
            ctx=ctx,
            op=op,
            plane=Plane.DATA,
            all_ops=self.op_sets.all_data,
            cached_ops=cached_coverage.data,
        )

        return count

    def _count_wildcard_coverage(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        all_ops: frozenset[str],
        cached_ops: set[str],
    ) -> int:
        """Count coverage for a single wildcard pattern in one plane."""
        key = plane.make_key(op)
        pattern_ops = get_matching_operations(op, all_ops, plane, caches=self._caches)

        if key in ctx.wildcard_partial_coverage:
            return len(cached_ops & pattern_ops)

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

    def get_cached_coverage(self, role_id: str) -> RoleCoverage | None:
        """Get cached coverage for a role, if available."""
        return self._caches.role_coverage.get(role_id)

    def is_high_privilege(self, role_id: str) -> bool:
        """Check if a role is high-privilege (O(1) cache lookup).

        A role is high-privilege if it can assign ANY role without condition.
        Returns False for roles not in cache (ad-hoc/test roles).
        """
        return role_id in self._caches.high_privilege_roles

    def expand_missing_operations(
        self,
        ctx: RoleEvaluationContext,
        missing_ops: set[str],
        classified: ClassifiedOperations,
        cached_coverage: RoleCoverage,
    ) -> ExpandedMissing:
        """Expand missing operations to show individual uncovered operations.

        For wildcards, this expands to show which specific operations are not covered.
        Returns operations with original casing for display.
        """
        expanded: list[str] = []
        total_count = 0

        for op in missing_ops:
            if not is_wildcard_pattern(op):
                # Restore original casing for explicit operations
                expanded.append(self._ops_lowered_to_orig.get(op, op))
                total_count += 1
                continue

            result = self._expand_wildcard_missing(ctx, op, classified, cached_coverage)
            if result.operations:
                # Restore original casing for expanded operations
                restored = [self._ops_lowered_to_orig.get(op, op) for op in result.operations]
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
        cached_coverage: RoleCoverage,
    ) -> ExpandedMissing:
        """Expand a wildcard pattern to its uncovered operations."""
        expanded: list[str] = []
        total = 0

        # Control plane
        if op in classified.control_wildcards:
            ctrl = self._get_plane_uncovered(
                ctx=ctx,
                op=op,
                plane=Plane.CONTROL,
                all_ops=self.op_sets.all_control,
                cached_ops=cached_coverage.control,
            )
            expanded.extend(ctrl.operations)
            total += ctrl.total

        # Data plane
        if op in classified.data_wildcards:
            data = self._get_plane_uncovered(
                ctx=ctx,
                op=op,
                plane=Plane.DATA,
                all_ops=self.op_sets.all_data,
                cached_ops=cached_coverage.data,
            )
            expanded.extend(data.operations)
            total += data.total

        return ExpandedMissing(expanded, total)

    def _get_plane_uncovered(
        self,
        ctx: RoleEvaluationContext,
        op: str,
        plane: Plane,
        all_ops: frozenset[str],
        cached_ops: set[str],
    ) -> ExpandedMissing:
        """Get uncovered operations for a wildcard in one plane."""
        key = plane.make_key(op)
        pattern_ops = get_matching_operations(op, all_ops, plane, caches=self._caches)

        if key in ctx.wildcard_partial_coverage:
            covered = cached_ops & pattern_ops
            uncovered = pattern_ops - covered
            uncovered_samples = sorted(uncovered)[:DEFAULT_SEARCH_LIMIT]
            return ExpandedMissing(uncovered_samples, len(uncovered))

        if key not in ctx.fully_covered_wildcards:
            return ExpandedMissing(sorted(pattern_ops), len(pattern_ops))

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
