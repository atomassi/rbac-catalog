"""Role matching functions for pattern-based permission analysis."""

from __future__ import annotations

import logging
from collections.abc import Set as AbstractSet
from itertools import islice
from typing import TYPE_CHECKING, Final

from azurerbac.core.constants import MAX_UNCOVERED_SAMPLE
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern
from azurerbac.matching.models import (
    PartialCoverageCacheKey,
    PatternCacheKey,
    Plane,
    WildcardCoverageResult,
)

if TYPE_CHECKING:
    from azurerbac.cache import CacheData

logger = logging.getLogger(__name__)

type OperationName = str
type Pattern = str

_EXTENDED_SAMPLE_SIZE: Final[int] = 100


def _get_cache(caches: CacheData | None = None) -> CacheData:
    """Get cache data, using provided override or global singleton.

    This function centralizes cache access, making it easier to mock in tests
    and reducing scattered singleton access throughout the module.
    """
    if caches is not None:
        return caches
    from azurerbac.cache import get_cache_service

    return get_cache_service().container.cache


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


def pattern_covers_pattern(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a role's action pattern covers a requested wildcard pattern."""
    if role_pattern in ("*", requested_pattern):
        return True

    return (
        _suffix_pattern_covers(role_pattern, requested_pattern)
        or _prefix_pattern_covers(role_pattern, requested_pattern)
        or _segment_pattern_covers(role_pattern, requested_pattern)
    )


def operation_matches_any_pattern(operation: OperationName, patterns: list[Pattern]) -> bool:
    """Check if an operation matches any of the given patterns."""
    for pattern in patterns:
        if pattern == "*":
            return True
        if matches_pattern(operation, pattern):
            return True
    return False


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

    Args:
        pattern: The pattern to match operations against.
        all_operations: Set of all operation names to search.
        plane: Optional plane for cache lookup (CONTROL or DATA).
        caches: Optional cache container (defaults to global singleton).
    """
    cache = _get_cache(caches)

    key = PatternCacheKey(pattern.lower(), plane) if plane is not None else None
    if key is not None and (cached := cache.pattern_match.get(key)) is not None:
        return cached

    # Find matching operations
    matching = {op for op in all_operations if matches_pattern(op, pattern)}

    if key is not None:
        cache.pattern_match[key] = matching

    return matching


def has_any_wildcard_coverage(
    requested_pattern: str,
    actions: list[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    plane: Plane | None = None,
    *,
    caches: CacheData | None = None,
) -> bool:
    """Fast check if actions provide ANY coverage for a wildcard pattern."""
    matching_ops = get_matching_operations(requested_pattern, all_operations, plane, caches=caches)
    if not matching_ops:
        return False

    # Check extended sample for any allowed operation (islice avoids list conversion)
    sample = islice(matching_ops, _EXTENDED_SAMPLE_SIZE)
    return any(check_operation_allowed(op, actions, not_actions) for op in sample)


def check_wildcard_operation_allowed(
    requested_pattern: str,
    actions: list[str],
    not_actions: list[str],
) -> bool:
    """Check if a wildcard pattern is fully covered by the given actions/notActions.

    A wildcard pattern is covered if:
    1. At least one action pattern covers the entire requested pattern
    2. No notAction pattern excludes any part of the requested pattern

    This is more conservative - we only say it's covered if the role
    definitely grants all operations matching the requested pattern.
    """
    # Check if any action pattern covers the requested pattern
    if not any(pattern_covers_pattern(action, requested_pattern) for action in actions):
        return False

    # Check if any notAction might exclude parts of the requested pattern
    # If a notAction overlaps with the requested pattern, we can't guarantee full coverage

    # Pre-compute requested pattern parts for overlap check (avoid repeated work in loop)
    req_is_wildcard = is_wildcard_pattern(requested_pattern)
    req_suffix = ""
    req_prefix = ""
    if req_is_wildcard:
        req_parts = requested_pattern.split("*")
        req_suffix = req_parts[-1].lower()  # e.g., "/read"
        req_prefix = req_parts[0].lower()

    for not_action in not_actions:
        # If notAction covers the requested pattern, it's excluded
        if pattern_covers_pattern(not_action, requested_pattern):
            return False
        # If notAction could match some operations in the requested pattern
        # we're conservative and say it's not fully covered
        if req_is_wildcard and is_wildcard_pattern(not_action):
            # Check if patterns could possibly overlap (match same operations)
            # For patterns like */read and Microsoft.Authorization/*/Delete:
            # - */read matches anything ending in /read
            # - Microsoft.Authorization/*/Delete matches Authorization resources with /Delete
            # These don't overlap because the suffixes are different

            # Get the suffix after the last wildcard and prefix before first wildcard
            not_parts = not_action.split("*")
            not_suffix = not_parts[-1].lower()  # e.g., "/delete"
            not_prefix = not_parts[0].lower()

            # Patterns overlap if:
            # 1. One suffix is empty OR suffixes are compatible (one could match the other)
            # 2. AND one prefix is empty OR prefixes are compatible

            # Check suffix compatibility
            suffixes_compatible = (
                not req_suffix
                or not not_suffix  # One is empty (like * pattern)
                or req_suffix == not_suffix  # Same suffix
                or req_suffix.endswith(not_suffix)
                or not_suffix.endswith(req_suffix)
            )

            # Check prefix compatibility
            prefixes_compatible = (
                not req_prefix
                or not not_prefix  # One is empty
                or req_prefix.startswith(not_prefix)
                or not_prefix.startswith(req_prefix)
            )

            # Only consider patterns overlapping if BOTH prefix and suffix are compatible
            if suffixes_compatible and prefixes_compatible:
                return False

    return True


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
) -> WildcardCoverageResult:
    """Count how many operations matching a wildcard pattern are granted by the actions.

    Args:
        requested_pattern: The wildcard pattern to check coverage for.
        actions: List of action patterns from the role.
        not_actions: List of notAction patterns from the role.
        all_operations: Set of all valid operations.
        plane: Optional plane for cache lookup (CONTROL or DATA).
        max_uncovered_sample: Maximum uncovered operations to sample.
        caches: Optional cache container.

    Returns:
        WildcardCoverageResult with coverage statistics.
    """
    cache = _get_cache(caches)
    partial_cache_key = PartialCoverageCacheKey.build(
        requested_pattern, plane, actions, not_actions
    )

    # Check cache
    if partial_cache_key is not None:
        cached = cache.partial_coverage.get(partial_cache_key)
        if cached is not None:
            return cached

    # Get matching operations
    matching_ops = get_matching_operations(requested_pattern, all_operations, plane, caches=cache)
    total_count = len(matching_ops)

    if total_count == 0:
        result = WildcardCoverageResult(0, 0, 0, [])
        if partial_cache_key is not None:
            cache.partial_coverage[partial_cache_key] = result
        return result

    # Compute covered operations using helper functions
    covered_by_actions = _compute_covered_operations(
        actions, matching_ops, all_operations, plane, cache
    )
    covered_by_actions = _remove_excluded_operations(
        covered_by_actions, not_actions, all_operations, plane, cache
    )

    # Calculate coverage
    covered_ops = matching_ops & covered_by_actions
    covered_count = len(covered_ops)
    uncovered_ops = matching_ops - covered_ops
    uncovered_count = len(uncovered_ops)
    uncovered_samples = sorted(uncovered_ops)[:max_uncovered_sample]

    result = WildcardCoverageResult(covered_count, total_count, uncovered_count, uncovered_samples)
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
    """Count the net number of operations granted (actions minus notActions).

    Args:
        actions: List of action patterns that grant access.
        not_actions: List of notAction patterns that deny access.
        all_operations: Set of all valid operations.
        plane: Optional plane for cache lookups (CONTROL or DATA).
        caches: Optional cache container.

    Returns:
        Net count of granted operations.
    """
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
