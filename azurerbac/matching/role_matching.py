"""Role matching functions for pattern-based permission analysis."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Final

from azurerbac.cache import CacheData, get_cache_service
from azurerbac.core.constants import MAX_UNCOVERED_SAMPLE
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern

# Type aliases for readability
type OperationName = str
type Pattern = str

# Constants for sampling limits
_QUICK_SAMPLE_SIZE: Final[int] = 10
_EXTENDED_SAMPLE_SIZE: Final[int] = 100


def _suffix_pattern_covers(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a suffix pattern (*/suffix) covers the requested pattern."""
    role_suffix = role_pattern[1:]  # e.g., "/read"
    return requested_pattern.endswith(role_suffix)


def _prefix_pattern_covers(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a prefix pattern (prefix/*) covers the requested pattern."""
    role_prefix = role_pattern[:-1]  # e.g., "Microsoft.Storage/"
    return requested_pattern.startswith(role_prefix)


def _compare_segments(role_parts_lower: list[str], requested_parts_lower: list[str]) -> bool:
    """Compare pre-lowercased pattern segments to determine if role covers requested."""
    for i, role_seg in enumerate(role_parts_lower):
        if role_seg == "*":
            # Last segment is *, covers everything after
            if i == len(role_parts_lower) - 1:
                return True
            continue
        if i < len(requested_parts_lower):
            req_seg = requested_parts_lower[i]
            if req_seg == "*" and role_seg != "*":
                # Requested has wildcard, role has specific - role doesn't cover
                return False
            if req_seg not in (role_seg, "*"):
                return False
        else:
            return False
    return True


def pattern_covers_pattern(role_pattern: str, requested_pattern: str) -> bool:
    """Check if a role's action pattern covers a requested wildcard pattern.

    For example:
    - 'Microsoft.Storage/*' covers 'Microsoft.Storage/*/read' (role grants more)
    - '*' covers anything
    - '*/read' covers 'Microsoft.Storage/*/read' (anything ending in /read)
    - 'Microsoft.Storage/storageAccounts/*' covers 'Microsoft.Storage/storageAccounts/read'
    - 'Microsoft.Storage/storageAccounts/read' does NOT cover 'Microsoft.Storage/*/read'

    The logic: role_pattern covers requested_pattern if every operation that matches
    requested_pattern would also match role_pattern.
    """
    # Fast path: trivial matches
    if role_pattern in ("*", requested_pattern):
        return True

    # Handle suffix patterns like */read
    if role_pattern.startswith("*/") and _suffix_pattern_covers(role_pattern, requested_pattern):
        return True

    # Handle prefix patterns like Microsoft.Storage/*
    if role_pattern.endswith("/*") and _prefix_pattern_covers(role_pattern, requested_pattern):
        return True

    # Segment-by-segment comparison (pre-lowercase for O(1) comparison)
    role_parts_lower = [s.lower() for s in role_pattern.split("/")]
    requested_parts_lower = [s.lower() for s in requested_pattern.split("/")]

    # Role pattern must have same or fewer segments
    if len(role_parts_lower) > len(requested_parts_lower):
        return False

    return _compare_segments(role_parts_lower, requested_parts_lower)


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
    cache_key: int | None = None,
    *,
    caches: CacheData | None = None,
) -> set[str]:
    """Get all operations matching a pattern, with caching.

    This is the core optimization - we cache the result of pattern matching
    so subsequent calls with the same pattern are instant.

    Args:
        pattern: The pattern to match operations against
        all_operations: Set of all operation names to search
        cache_key: Optional key for cache lookup (typically operation count)
        caches: Optional cache container to use (defaults to get_cache_service().container.cache)
    """
    cache = caches if caches is not None else get_cache_service().container.cache

    key = (pattern.lower(), cache_key) if cache_key is not None else None
    if key is not None and key in cache.pattern_match:
        return cache.pattern_match[key]

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
    cache_key: int | None = None,
) -> bool:
    """Fast check if actions provide ANY coverage for a wildcard pattern."""
    matching_ops = get_matching_operations(requested_pattern, all_operations, cache_key)
    if not matching_ops:
        return False

    # Check if at least one operation is allowed (check quick sample first)
    sample = list(matching_ops)[:_QUICK_SAMPLE_SIZE]
    for op in sample:
        if check_operation_allowed(op, actions, not_actions):
            return True

    # If none of the first sample match, check extended sample
    if len(matching_ops) > _QUICK_SAMPLE_SIZE:
        sample = list(matching_ops)[_QUICK_SAMPLE_SIZE:_EXTENDED_SAMPLE_SIZE]
        for op in sample:
            if check_operation_allowed(op, actions, not_actions):
                return True

    return False


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
    covered = False
    for action in actions:
        if pattern_covers_pattern(action, requested_pattern):
            covered = True
            break

    if not covered:
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
    cache_key: int | None,
    cache: CacheData,
) -> set[str]:
    """Compute the set of operations covered by the given actions."""
    covered: set[str] = set()
    for action in actions:
        if action == "*":
            return matching_ops.copy()
        if is_wildcard_pattern(action):
            action_matches = get_matching_operations(
                action, all_operations, cache_key, caches=cache
            )
            covered.update(action_matches)
        elif action in all_operations:
            covered.add(action)
    return covered


def _remove_excluded_operations(
    covered: set[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None,
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
            not_matches = get_matching_operations(
                not_action, all_operations, cache_key, caches=cache
            )
            result -= not_matches
        else:
            result.discard(not_action)
    return result


def count_wildcard_partial_coverage(
    requested_pattern: str,
    actions: list[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None = None,
    max_uncovered_sample: int = MAX_UNCOVERED_SAMPLE,
) -> tuple[int, int, int, list[str]]:
    """Count how many operations matching a wildcard pattern are granted by the actions.

    Returns:
        (covered_count, total_count, uncovered_count, uncovered_sample):
        - covered_count: Number of operations covered by the actions
        - total_count: Total matching the pattern
        - uncovered_count: Number of uncovered operations
        - uncovered_sample: Sample list of uncovered operation names
    """
    cache = get_cache_service().container.cache
    partial_cache_key = None

    # Use cache if available
    if cache_key is not None:
        partial_cache_key = (
            requested_pattern,
            cache_key,
            tuple(sorted(actions)),
            tuple(sorted(not_actions)),
        )
        if partial_cache_key in cache.partial_coverage:
            return cache.partial_coverage[partial_cache_key]

    # Get matching operations from cache (fast after first call)
    matching_ops = get_matching_operations(
        requested_pattern, all_operations, cache_key, caches=cache
    )
    total_count = len(matching_ops)

    if total_count == 0:
        result = (0, 0, 0, [])
        if partial_cache_key is not None:
            cache.partial_coverage[partial_cache_key] = result
        return result

    # Compute covered operations using helper functions
    covered_by_actions = _compute_covered_operations(
        actions, matching_ops, all_operations, cache_key, cache
    )
    covered_by_actions = _remove_excluded_operations(
        covered_by_actions, not_actions, all_operations, cache_key, cache
    )

    # Calculate coverage
    covered_ops = matching_ops & covered_by_actions
    covered_count = len(covered_ops)
    uncovered_ops = matching_ops - covered_ops
    uncovered_count = len(uncovered_ops)
    uncovered_sample = sorted(uncovered_ops)[:max_uncovered_sample]

    result = (covered_count, total_count, uncovered_count, uncovered_sample)
    if partial_cache_key is not None:
        cache.partial_coverage[partial_cache_key] = result
    return result


def count_operations_matching_pattern(
    pattern: str, all_operations: AbstractSet[str], cache_key: int | None = None
) -> int:
    """Count how many actual operations match a pattern (explicit or wildcard)."""
    if not is_wildcard_pattern(pattern):
        # Explicit operation - either matches 1 or 0
        return 1 if pattern in all_operations else 0

    # For wildcards, count exact matches
    return count_wildcard_matches(pattern, all_operations, cache_key)


def count_wildcard_matches(
    pattern: str,
    all_operations: AbstractSet[str],
    cache_key: int | None = None,
    *,
    caches: CacheData | None = None,
) -> int:
    """Count how many operations match a wildcard pattern (exact count)."""
    cache = caches if caches is not None else get_cache_service().container.cache

    # Use the pattern match cache - this is fast after first call
    matching = get_matching_operations(pattern, all_operations, cache_key, caches=cache)
    count = len(matching)

    # Also update the count cache for compatibility
    if cache_key is not None:
        cache_entry = (pattern.lower(), cache_key)
        cache.wildcard_count[cache_entry] = count

    return count


def _count_all_actions_grant(
    not_actions: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None,
) -> int:
    """Count when actions contains '*' (grants all operations)."""
    if not not_actions:
        return len(all_operations)
    excluded = sum(
        (
            count_wildcard_matches(p, all_operations, cache_key)
            if is_wildcard_pattern(p)
            else (1 if p in all_operations else 0)
        )
        for p in not_actions
    )
    return max(0, len(all_operations) - excluded)


def _count_action_grants(
    actions: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None,
) -> tuple[int, list[str], list[str]]:
    """Count grants from explicit and wildcard actions."""
    explicit_actions = [a for a in actions if not is_wildcard_pattern(a)]
    wildcard_patterns = [a for a in actions if is_wildcard_pattern(a)]
    count = len(explicit_actions)
    for pattern in wildcard_patterns:
        count += count_wildcard_matches(pattern, all_operations, cache_key)
    return count, explicit_actions, wildcard_patterns


def _subtract_not_actions(
    count: int,
    not_actions: list[str],
    explicit_actions: list[str],
    wildcard_patterns: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None,
) -> int:
    """Subtract exclusions from notActions."""
    for not_pattern in not_actions:
        if is_wildcard_pattern(not_pattern):
            count -= count_wildcard_matches(not_pattern, all_operations, cache_key)
        elif not_pattern in explicit_actions or any(
            matches_pattern(not_pattern, wp) for wp in wildcard_patterns
        ):
            count -= 1
    return max(0, count)


def count_net_permissions(
    actions: list[str],
    not_actions: list[str],
    all_operations: AbstractSet[str],
    cache_key: int | None = None,
) -> int:
    """Count the net number of operations granted (actions minus notActions).

    This provides a count for ranking purposes.

    For explicit actions (no wildcards), we count them directly.
    For wildcard patterns, we estimate matching known operations.
    Then we subtract any operations excluded by notActions.
    """
    if not actions:
        return 0

    # Handle wildcard * that matches everything
    if "*" in actions:
        return _count_all_actions_grant(not_actions, all_operations, cache_key)

    # Count explicit and wildcard actions
    count, explicit_actions, wildcard_patterns = _count_action_grants(
        actions, all_operations, cache_key
    )

    # Subtract exclusions
    if not_actions:
        count = _subtract_not_actions(
            count, not_actions, explicit_actions, wildcard_patterns, all_operations, cache_key
        )

    return count
