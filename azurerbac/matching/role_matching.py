"""Role matching functions for pattern-based permission analysis."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING

from azurerbac.core.constants import (
    HIGH_PRIVILEGE_OPERATION,
    HIGH_PRIVILEGE_ROLE_IDS,
)
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern
from azurerbac.matching.models import (
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


def operation_matches_any_pattern(operation: OperationName, patterns: Iterable[Pattern]) -> bool:
    """Check if an operation matches any of the given patterns."""
    return any(p == "*" or matches_pattern(operation, p) for p in patterns)


def check_operation_allowed(
    operation: str,
    actions: Iterable[str],
    not_actions: Iterable[str],
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
        # Use generator expressions to avoid allocating temporary lists
        actions_lower = (a.lower() for a in perm.actions)
        not_actions_lower = (a.lower() for a in perm.not_actions)
        if check_operation_allowed(
            HIGH_PRIVILEGE_OPERATION,
            actions_lower,
            not_actions_lower,
        ):
            return True

    return False
