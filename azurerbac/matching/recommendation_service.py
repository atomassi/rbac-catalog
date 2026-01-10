"""Role Recommendation Service - orchestrates role matching with focused methods.

This module provides the RoleRecommendationService class which encapsulates
all role recommendation logic with single-responsibility methods.
"""

from __future__ import annotations

import logging
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

from azurerbac.azure.models import OperationData, Permission, RoleDefinition
from azurerbac.cache import get_cache_container
from azurerbac.core.constants import DEFAULT_SEARCH_LIMIT
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching.role_matching import (
    check_operation_allowed,
    check_wildcard_operation_allowed,
    count_net_permissions,
    count_operations_matching_pattern,
    count_wildcard_partial_coverage,
    get_matching_operations,
    has_any_wildcard_coverage,
)
from azurerbac.matching.types import ClassifiedOperations, OperationSets

logger = logging.getLogger(__name__)

# Type aliases
WildcardKey = str  # "ctrl:pattern" or "data:pattern"
PartialCoverageInfo = tuple[int, int, int, list[str]]  # (covered, total, uncovered, samples)


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


class RoleRecommendationService:
    """Service for recommending least-privilege roles based on requested operations.

    This class breaks down the monolithic recommend_roles function into focused methods:
    - classify_operations: Separate operations by plane and type
    - compute_wildcard_matches: Pre-compute wildcard pattern matches
    - evaluate_role: Check a single role's coverage
    - calculate_missing: Determine missing operations for a role
    - build_role_match: Construct the final RoleMatch object
    - sort_and_filter: Apply sorting and limit results
    """

    def __init__(
        self,
        all_operations: list[OperationData],
        requested_ops_data_flags: dict[str, bool] | None = None,
    ) -> None:
        """Initialize the service with operation data.

        Args:
            all_operations: List of all known operations as OperationData objects
            requested_ops_data_flags: Optional explicit is_data_action flags
        """
        self.op_sets = OperationSets.from_operations(all_operations)
        self.requested_ops_data_flags = requested_ops_data_flags or {}
        self._caches = get_cache_container().cache

        # Pre-computed wildcard matches (populated by compute_wildcard_matches)
        self.control_wildcard_ops: dict[str, set[str]] = {}
        self.data_wildcard_ops: dict[str, set[str]] = {}

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

    def _evaluate_explicit_ops(
        self,
        ctx: RoleEvaluationContext,
        ops: AbstractSet[str],
        role_ops: set[str],
    ) -> None:
        """Evaluate explicit operations against role coverage."""
        ctx.matched_ops.update(ops & role_ops)

    def _evaluate_wildcard_ops(
        self,
        ctx: RoleEvaluationContext,
        patterns: AbstractSet[str],
        wildcard_ops_map: dict[str, set[str]],
        role_ops: set[str],
        plane_prefix: str,
    ) -> None:
        """Evaluate wildcard patterns against role coverage."""
        for pattern in patterns:
            pattern_ops = wildcard_ops_map.get(pattern, set())
            covered_ops = role_ops & pattern_ops
            if covered_ops:
                ctx.matched_ops.add(pattern)
                if len(covered_ops) == len(pattern_ops):
                    ctx.fully_covered_wildcards.add(f"{plane_prefix}:{pattern}")
                else:
                    ctx.wildcard_partial_coverage[f"{plane_prefix}:{pattern}"] = (
                        len(covered_ops),
                        len(pattern_ops),
                        len(pattern_ops) - len(covered_ops),
                        [],
                    )

    def evaluate_role_fast_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: tuple[set[str], set[str]],
    ) -> None:
        """Evaluate role coverage using cached coverage data (fast path).

        Modifies ctx in-place with matched operations and coverage info.
        """
        role_control_ops, role_data_ops = cached_coverage

        # Check explicit operations
        self._evaluate_explicit_ops(ctx, classified.control, role_control_ops)
        self._evaluate_explicit_ops(ctx, classified.data, role_data_ops)

        # Check wildcard patterns
        self._evaluate_wildcard_ops(
            ctx, classified.control_wildcards, self.control_wildcard_ops, role_control_ops, "ctrl"
        )
        self._evaluate_wildcard_ops(
            ctx, classified.data_wildcards, self.data_wildcard_ops, role_data_ops, "data"
        )

        # Check for conditions using Permission model property
        ctx.has_conditions = any(p.has_condition for p in ctx.permissions)

    def evaluate_role_slow_path(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
        cached_coverage: tuple[set[str], set[str]] | None,
    ) -> None:
        """Evaluate role coverage by iterating permissions (slow path).

        Used when full cache is not available.
        """
        for perm in ctx.permissions:
            actions = perm.actions
            not_actions = perm.not_actions
            data_actions = perm.data_actions
            not_data_actions = perm.not_data_actions
            perm_has_condition = perm.has_condition

            # Check explicit control operations
            for op in classified.control:
                if check_operation_allowed(op, actions, not_actions):
                    ctx.matched_ops.add(op)
                    if perm_has_condition:
                        ctx.has_conditions = True

            # Check control wildcards
            self._check_wildcard_coverage(
                ctx=ctx,
                patterns=classified.control_wildcards,
                plane="ctrl",
                allowed_actions=actions,
                not_actions=not_actions,
                all_ops=self.op_sets.all_control,
                cache_key=self.op_sets.control_cache_key,
                wildcard_ops_map=self.control_wildcard_ops,
                cached_coverage=(cached_coverage[0] if cached_coverage else None),
                perm_has_condition=perm_has_condition,
            )

            # Check explicit data operations
            for op in classified.data:
                if check_operation_allowed(op, data_actions, not_data_actions):
                    ctx.matched_ops.add(op)
                    if perm_has_condition:
                        ctx.has_conditions = True

            # Check data wildcards
            self._check_wildcard_coverage(
                ctx=ctx,
                patterns=classified.data_wildcards,
                plane="data",
                allowed_actions=data_actions,
                not_actions=not_data_actions,
                all_ops=self.op_sets.all_data,
                cache_key=self.op_sets.data_cache_key,
                wildcard_ops_map=self.data_wildcard_ops,
                cached_coverage=(cached_coverage[1] if cached_coverage else None),
                perm_has_condition=perm_has_condition,
            )

    def _check_wildcard_coverage(
        self,
        ctx: RoleEvaluationContext,
        patterns: frozenset[str],
        plane: str,
        allowed_actions: list[str],
        not_actions: list[str],
        all_ops: frozenset[str],
        cache_key: int,
        wildcard_ops_map: dict[str, set[str]],
        cached_coverage: set[str] | None,
        perm_has_condition: bool,
    ) -> None:
        """Check wildcard pattern coverage for a single plane."""
        for pattern in patterns:
            key = f"{plane}:{pattern}"

            if check_wildcard_operation_allowed(pattern, allowed_actions, not_actions):
                ctx.matched_ops.add(pattern)
                ctx.fully_covered_wildcards.add(key)
                if perm_has_condition:
                    ctx.has_conditions = True
            elif key not in ctx.wildcard_partial_coverage:
                # Check for partial coverage
                if cached_coverage:
                    pattern_ops = wildcard_ops_map.get(pattern, set())
                    has_coverage = bool(cached_coverage & pattern_ops)
                else:
                    has_coverage = has_any_wildcard_coverage(
                        pattern, allowed_actions, not_actions, all_ops, cache_key
                    )

                if has_coverage:
                    ctx.wildcard_partial_coverage[key] = (1, 0, 0, [])
                    ctx.matched_ops.add(pattern)
                    if perm_has_condition:
                        ctx.has_conditions = True

    def calculate_missing_ops(
        self,
        ctx: RoleEvaluationContext,
        classified: ClassifiedOperations,
    ) -> set[str]:
        """Calculate which requested operations are not covered by the role."""
        missing = set()
        requested_set = classified.all_requested

        for op in requested_set:
            if op not in ctx.matched_ops:
                missing.add(op)
            elif is_wildcard_pattern(op):
                in_control = op in classified.control_wildcards
                in_data = op in classified.data_wildcards

                if in_control and f"ctrl:{op}" not in ctx.fully_covered_wildcards:
                    missing.add(op)
                if in_data and f"data:{op}" not in ctx.fully_covered_wildcards:
                    missing.add(op)

        return missing

    def calculate_matched_ops_count(
        self,
        ctx: RoleEvaluationContext,
        cached_coverage: tuple[set[str], set[str]] | None,
    ) -> int:
        """Calculate the total count of matched operations (expanding wildcards)."""
        count = 0
        actions, not_actions, data_actions, not_data_actions = self._extract_permission_lists(
            ctx.permissions
        )

        for op in ctx.matched_ops:
            if not is_wildcard_pattern(op):
                count += 1
                continue

            ctrl_key = f"ctrl:{op}"
            data_key = f"data:{op}"

            # Control plane
            if ctrl_key in ctx.wildcard_partial_coverage:
                if cached_coverage:
                    pattern_ops = get_matching_operations(
                        op, self.op_sets.all_control, self.op_sets.control_cache_key
                    )
                    count += len(cached_coverage[0] & pattern_ops)
                else:
                    covered, _, _, _ = count_wildcard_partial_coverage(
                        op,
                        actions,
                        not_actions,
                        self.op_sets.all_control,
                        self.op_sets.control_cache_key,
                    )
                    count += covered
            elif ctrl_key in ctx.fully_covered_wildcards:
                count += count_operations_matching_pattern(
                    op, self.op_sets.all_control, self.op_sets.control_cache_key
                )

            # Data plane
            if data_key in ctx.wildcard_partial_coverage:
                if cached_coverage:
                    pattern_ops = get_matching_operations(
                        op, self.op_sets.all_data, self.op_sets.data_cache_key
                    )
                    count += len(cached_coverage[1] & pattern_ops)
                else:
                    covered, _, _, _ = count_wildcard_partial_coverage(
                        op,
                        data_actions,
                        not_data_actions,
                        self.op_sets.all_data,
                        self.op_sets.data_cache_key,
                    )
                    count += covered
            elif data_key in ctx.fully_covered_wildcards:
                count += count_operations_matching_pattern(
                    op, self.op_sets.all_data, self.op_sets.data_cache_key
                )

        return count

    def _extract_permission_lists(
        self, permissions: list[Permission]
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        """Extract all permission lists from role permissions.

        Aggregates all action lists from Permission objects.
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
            )
            data_count += count_net_permissions(
                perm.data_actions,
                perm.not_data_actions,
                self.op_sets.all_data,
                self.op_sets.data_cache_key,
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
        actions, not_actions, data_actions, not_data_actions = self._extract_permission_lists(
            ctx.permissions
        )

        expanded: list[str] = []
        total_count = 0

        for op in missing_ops:
            if not is_wildcard_pattern(op):
                expanded.append(op)
                total_count += 1
                continue

            # Expand wildcard patterns
            ctrl_key = f"ctrl:{op}"
            data_key = f"data:{op}"
            has_partial_info = False

            # Control plane
            if ctrl_key in ctx.wildcard_partial_coverage:
                _, _, uncovered_count, samples = count_wildcard_partial_coverage(
                    op,
                    actions,
                    not_actions,
                    self.op_sets.all_control,
                    self.op_sets.control_cache_key,
                )
                total_count += uncovered_count
                self._extend_unique_sorted(expanded, samples)
                has_partial_info = True
            elif op in classified.control_wildcards and ctrl_key not in ctx.fully_covered_wildcards:
                matching = get_matching_operations(
                    op, self.op_sets.all_control, self.op_sets.control_cache_key
                )
                total_count += len(matching)
                self._extend_unique_sorted(expanded, matching)
                has_partial_info = True

            # Data plane
            if data_key in ctx.wildcard_partial_coverage:
                _, _, uncovered_count, samples = count_wildcard_partial_coverage(
                    op,
                    data_actions,
                    not_data_actions,
                    self.op_sets.all_data,
                    self.op_sets.data_cache_key,
                )
                total_count += uncovered_count
                self._extend_unique_sorted(expanded, samples)
                has_partial_info = True
            elif op in classified.data_wildcards and data_key not in ctx.fully_covered_wildcards:
                matching = get_matching_operations(
                    op, self.op_sets.all_data, self.op_sets.data_cache_key
                )
                total_count += len(matching)
                self._extend_unique_sorted(expanded, matching)
                has_partial_info = True

            if not has_partial_info:
                expanded.append(op)
                total_count += 1

        return expanded, total_count

    def _extend_unique_sorted(
        self,
        dst: list[str],
        values: set[str] | list[str],
        *,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> None:
        """Extend list with unique sorted values up to limit."""
        for v in sorted(values)[:limit]:
            if v not in dst:
                dst.append(v)

    def has_full_cache(self) -> bool:
        """Check if full role coverage cache is available."""
        return len(self._caches.role_coverage) > 0

    def get_cached_coverage(self, role_id: str) -> tuple[set[str], set[str]] | None:
        """Get cached coverage for a role, if available."""
        return self._caches.role_coverage.get(role_id)

    def finalize_partial_coverage(self, ctx: RoleEvaluationContext) -> None:
        """Add partially covered wildcards to matched_ops for display."""
        for key, (covered, _, _, _) in ctx.wildcard_partial_coverage.items():
            pattern = key.split(":", 1)[1] if ":" in key else key
            if pattern not in ctx.matched_ops and covered > 0:
                ctx.matched_ops.add(pattern)

    def is_builtin_role(self, role: RoleDefinition) -> bool:
        """Check if a role is a built-in role."""
        return role.is_builtin

    def extract_role_info(self, role: RoleDefinition) -> tuple[str, str, str, list[Permission]]:
        """Extract role information from a RoleDefinition.

        Returns:
            Tuple of (role_id, role_name, description, permissions)
        """
        return (
            role.role_id,
            role.role_name,
            role.description,
            role.properties.permissions,
        )
