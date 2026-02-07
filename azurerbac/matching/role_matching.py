"""Role matching functions for pattern-based permission analysis."""

from __future__ import annotations

import heapq
import logging
from collections.abc import Set as AbstractSet
from functools import lru_cache
from typing import TYPE_CHECKING

from azurerbac.core.constants import (
    HIGH_PRIVILEGE_OPERATION,
    HIGH_PRIVILEGE_ROLE_IDS,
    MAX_UNCOVERED_SAMPLE,
)
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern
from azurerbac.matching.models import (
    CoverageResult,
    PartialCoverageCacheKey,
    PatternCacheKey,
    Plane,
)

if TYPE_CHECKING:
    from azurerbac.azure.models import RoleDefinition
    from azurerbac.cache import CacheData

logger = logging.getLogger(__name__)

type OperationName = str
type Pattern = str


def _get_cache(caches: CacheData | None = None) -> CacheData:
    """Get cache data, using provided override or global singleton.

    This function centralizes cache access, making it easier to mock in tests
    and reducing scattered singleton access throughout the module.
    """
    if caches is not None:
        return caches
    from azurerbac.cache import get_cache_service

    return get_cache_service().cache


def _suffix_pattern_covers(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a suffix pattern (*/suffix) covers the requested pattern.

    Example: '*/read' covers 'Microsoft.Storage/accounts/read'
    """
    if not role_pattern.startswith("*/"):
        return False
    return requested_pattern.endswith(role_pattern[1:])


def _prefix_pattern_covers(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a prefix pattern (prefix/*) covers the requested pattern.

    Example: 'Microsoft.Storage/*' covers 'Microsoft.Storage/accounts/read'
    """
    if not role_pattern.endswith("/*"):
        return False
    return requested_pattern.startswith(role_pattern[:-1])


def _segment_pattern_covers(role_pattern: str, requested_pattern: str) -> bool:
    """Check if role pattern covers requested by comparing path segments.

    Example: 'Microsoft.Storage/*/read' covers 'Microsoft.Storage/accounts/read'
    """
    role_parts = role_pattern.lower().split("/")
    requested_parts = requested_pattern.lower().split("/")

    if len(role_parts) > len(requested_parts):
        return False

    for i, role_seg in enumerate(role_parts):
        if role_seg == "*":
            # Trailing wildcard matches everything after
            if i == len(role_parts) - 1:
                return True
            continue

        if i >= len(requested_parts):
            return False

        req_seg = requested_parts[i]
        # Requested has wildcard but role has specific - role can't cover
        if req_seg == "*" and role_seg != "*":
            return False
        # Segments must match (or role has wildcard, handled above)
        if req_seg not in (role_seg, "*"):
            return False

    return True


@lru_cache(maxsize=50000)
def pattern_covers_pattern(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a role's action pattern covers a requested wildcard pattern.

    Memoized with LRU cache since pattern-to-pattern relationships are
    immutable and frequently recomputed during role matching.
    """
    if role_pattern in ("*", requested_pattern):
        return True

    return (
        _suffix_pattern_covers(role_pattern, requested_pattern)
        or _prefix_pattern_covers(role_pattern, requested_pattern)
        or _segment_pattern_covers(role_pattern, requested_pattern)
    )


def operation_matches_any_pattern(operation: OperationName, patterns: list[Pattern]) -> bool:
    """Check if an operation matches any of the given patterns."""
    return any(p == "*" or matches_pattern(operation, p) for p in patterns)


def check_operation_allowed(
    operation: str,
    actions: list[str],
    not_actions: list[str],
) -> bool:
    """Check if a specific operation is allowed by the given actions/notActions.

    An operation is allowed if:
    1. It matches at least one pattern in actions
    2. It does NOT match any pattern in notActions
    """
    # Check if operation matches any action pattern
    if not operation_matches_any_pattern(operation, actions):
        return False

    # Check if operation is excluded by notActions
    return not operation_matches_any_pattern(operation, not_actions)


def get_matching_operations(
    pattern: str,
    all_operations: AbstractSet[str],
    plane: Plane | None = None,
    *,
    caches: CacheData | None = None,
) -> set[str]:
    """Get all operations matching a pattern, with caching.

    This is the core optimization - we cache the result of pattern matching
    so subsequent calls with the same pattern are instant.

    Note: Returns lowered operation names.
    Expects all_operations to contain lowered names.

    Args:
        pattern: The pattern to match operations against.
        all_operations: Set of all operation names to search (lowered).
        plane: Optional plane for cache lookup (CONTROL or DATA).
        caches: Optional cache container (defaults to global singleton).
    """
    cache = _get_cache(caches)

    key = PatternCacheKey(pattern.lower(), plane) if plane is not None else None
    if key is not None and (cached := cache.pattern_match.get(key)) is not None:
        return cached

    # Find matching operations - result is already lowered since all_operations is lowered
    matching = {op for op in all_operations if matches_pattern(op, pattern)}

    if key is not None:
        cache.pattern_match[key] = matching

    return matching


def _compute_covered_operations(
    actions: list[str],
    matching_ops: set[str],
    all_operations: AbstractSet[str],
    plane: Plane | None,
    cache: CacheData,
) -> set[str]:
    """Compute the set of operations covered by the given actions."""
    covered: set[str] = set()
    for action in actions:
        if action == "*":
            return matching_ops.copy()
        if is_wildcard_pattern(action):
            action_matches = get_matching_operations(action, all_operations, plane, caches=cache)
            covered.update(action_matches)
        elif action in all_operations:
            covered.add(action)
    return covered


def _remove_excluded_operations(
    covered: set[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    plane: Plane | None,
    cache: CacheData,
) -> set[str]:
    """Remove operations excluded by notActions from the covered set."""
    if not not_actions:
        return covered
    result = covered.copy()
    for not_action in not_actions:
        if not_action == "*":
            return set()
        if is_wildcard_pattern(not_action):
            not_matches = get_matching_operations(not_action, all_operations, plane, caches=cache)
            result -= not_matches
        else:
            result.discard(not_action)
    return result


def count_wildcard_partial_coverage(
    requested_pattern: str,
    actions: list[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    plane: Plane | None = None,
    max_uncovered_sample: int = MAX_UNCOVERED_SAMPLE,
    *,
    caches: CacheData | None = None,
) -> CoverageResult:
    """Count operations matching a wildcard pattern that are granted by the given actions."""
    cache = _get_cache(caches)
    partial_cache_key = PartialCoverageCacheKey.build(
        requested_pattern, plane, actions, not_actions
    )

    if partial_cache_key is not None:
        cached = cache.partial_coverage.get(partial_cache_key)
        if cached is not None:
            return cached

    matching_ops = get_matching_operations(requested_pattern, all_operations, plane, caches=cache)
    total_count = len(matching_ops)

    if total_count == 0:
        result = CoverageResult(0, 0, 0, [])
        if partial_cache_key is not None:
            cache.partial_coverage[partial_cache_key] = result
        return result

    covered_by_actions = _compute_covered_operations(
        actions, matching_ops, all_operations, plane, cache
    )
    covered_by_actions = _remove_excluded_operations(
        covered_by_actions, not_actions, all_operations, plane, cache
    )

    covered_ops = matching_ops & covered_by_actions
    covered_count = len(covered_ops)
    uncovered_ops = matching_ops - covered_ops
    uncovered_count = len(uncovered_ops)
    uncovered_samples = heapq.nsmallest(max_uncovered_sample, uncovered_ops)

    result = CoverageResult(covered_count, total_count, uncovered_count, uncovered_samples)
    if partial_cache_key is not None:
        cache.partial_coverage[partial_cache_key] = result
    return result


def count_operations_matching_pattern(
    pattern: str, all_operations: AbstractSet[str], plane: Plane | None = None
) -> int:
    """Count how many actual operations match a pattern (explicit or wildcard)."""
    if not is_wildcard_pattern(pattern):
        return 1 if pattern in all_operations else 0
    return count_wildcard_matches(pattern, all_operations, plane)


def count_wildcard_matches(
    pattern: str,
    all_operations: AbstractSet[str],
    plane: Plane | None = None,
    *,
    caches: CacheData | None = None,
) -> int:
    """Count how many operations match a wildcard pattern (exact count)."""
    cache = _get_cache(caches)
    matching = get_matching_operations(pattern, all_operations, plane, caches=cache)
    count = len(matching)

    if plane is not None:
        cache.wildcard_count[PatternCacheKey(pattern.lower(), plane)] = count

    return count


def _count_pattern(
    pattern: str,
    all_operations: AbstractSet[str],
    plane: Plane | None,
    cache: CacheData,
) -> int:
    """Count matching operations for a pattern (explicit or wildcard)."""
    if is_wildcard_pattern(pattern):
        return count_wildcard_matches(pattern, all_operations, plane, caches=cache)
    return 1 if pattern in all_operations else 0


def count_net_permissions(
    actions: list[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    plane: Plane | None = None,
    *,
    caches: CacheData | None = None,
) -> int:
    """Count the net number of operations granted (actions minus notActions)."""
    if not actions:
        return 0

    cache = _get_cache(caches)

    # Handle wildcard * that matches everything
    if "*" in actions:
        if not not_actions:
            return len(all_operations)
        excluded = sum(_count_pattern(p, all_operations, plane, cache) for p in not_actions)
        return max(0, len(all_operations) - excluded)

    # Separate explicit and wildcard actions
    explicit = [a for a in actions if not is_wildcard_pattern(a)]
    wildcards = [a for a in actions if is_wildcard_pattern(a)]

    # Count total granted
    count = len(explicit) + sum(_count_pattern(p, all_operations, plane, cache) for p in wildcards)

    # Subtract exclusions
    for not_pattern in not_actions:
        if is_wildcard_pattern(not_pattern):
            count -= _count_pattern(not_pattern, all_operations, plane, cache)
        elif not_pattern in explicit or any(matches_pattern(not_pattern, wp) for wp in wildcards):
            count -= 1

    return max(0, count)


def is_high_privilege_role(role: RoleDefinition) -> bool:
    """Check if a role is high-privilege based on its ID or effective permissions.

    High-privilege: well-known role IDs OR grants roleAssignments/write without ABAC condition.
    """
    # Fast path: check well-known high-privilege role IDs
    if role.role_id in HIGH_PRIVILEGE_ROLE_IDS:
        return True

    for perm in role.properties.permissions:
        # Skip if condition constrains roleAssignments/write (e.g., RBAC Admin)
        if perm.condition and HIGH_PRIVILEGE_OPERATION in perm.condition.lower():
            continue

        # Check if this block allows roleAssignments/write
        if check_operation_allowed(
            HIGH_PRIVILEGE_OPERATION,
            [a.lower() for a in perm.actions],
            [a.lower() for a in perm.not_actions],
        ):
            return True

    return False
