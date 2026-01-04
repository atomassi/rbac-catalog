"""Cache precomputation functions."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from azurerbac.azure.roles import get_permission_actions, get_role_id, get_role_properties
from azurerbac.cache.models import CacheData, CacheMetadata, build_indexes
from azurerbac.cache.utils import build_operations_prefix_index, get_matching_operations
from azurerbac.core.patterns import is_wildcard_pattern

if TYPE_CHECKING:
    from azurerbac.cache.app_cache import AppCache

logger = logging.getLogger(__name__)


def _add_operations_for_patterns(
    dst: set[str],
    patterns: list[str],
    *,
    all_ops: set[str],
    cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
    ops_lower_to_orig: dict[str, str],
) -> None:
    for pattern in patterns:
        if pattern == "*":
            dst.update(all_ops)
        elif is_wildcard_pattern(pattern):
            dst.update(get_matching_operations(pattern, all_ops, cache_key, pattern_match))
        else:
            orig_op = ops_lower_to_orig.get(pattern.lower())
            if orig_op:
                dst.add(orig_op)


def _precompute_common_patterns(
    all_control_ops: set[str],
    all_data_ops: set[str],
    control_cache_key: int,
    data_cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
) -> None:
    """Precompute common wildcard patterns."""
    common_patterns = ["*/read", "*/write", "*/delete", "*/action", "*/listkeys/action", "*"]
    for pattern in common_patterns:
        get_matching_operations(pattern, all_control_ops, control_cache_key, pattern_match)
        get_matching_operations(pattern, all_data_ops, data_cache_key, pattern_match)


def _collect_role_patterns(roles: list[dict]) -> set[str]:
    """Collect all unique wildcard action patterns from roles."""
    patterns: set[str] = set()
    for role in roles:
        props = get_role_properties(role)
        for perm in props.get("permissions", []):
            actions, not_actions, data_actions, not_data_actions = get_permission_actions(perm)
            for action in actions + not_actions + data_actions + not_data_actions:
                if is_wildcard_pattern(action):
                    patterns.add(action.lower())
    return patterns


def _compute_role_coverage(
    role: dict,
    all_control_ops: set[str],
    all_data_ops: set[str],
    control_cache_key: int,
    data_cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
    control_ops_lower_to_orig: dict[str, str],
    data_ops_lower_to_orig: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Compute effective operations (granted - excluded) for a role."""
    props = get_role_properties(role)
    control_granted: set[str] = set()
    data_granted: set[str] = set()
    control_excluded: set[str] = set()
    data_excluded: set[str] = set()

    for perm in props.get("permissions", []):
        actions, not_actions, data_actions, not_data_actions = get_permission_actions(perm)

        _add_operations_for_patterns(
            control_granted,
            actions,
            all_ops=all_control_ops,
            cache_key=control_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            control_excluded,
            not_actions,
            all_ops=all_control_ops,
            cache_key=control_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_granted,
            data_actions,
            all_ops=all_data_ops,
            cache_key=data_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_excluded,
            not_data_actions,
            all_ops=all_data_ops,
            cache_key=data_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )

    return control_granted - control_excluded, data_granted - data_excluded


def _build_operation_role_count(
    role_coverage: dict[str, tuple[set[str], set[str]]],
) -> dict[str, int]:
    """Build operation -> role count index from role coverage."""
    counts: dict[str, int] = {}
    for control_ops, data_ops in role_coverage.values():
        for op in control_ops:
            op_lower = op.lower()
            counts[op_lower] = counts.get(op_lower, 0) + 1
        for op in data_ops:
            op_lower = op.lower()
            counts[op_lower] = counts.get(op_lower, 0) + 1
    return counts


def clear_computed_caches(cache: AppCache | None = None) -> None:
    """Clear all computed caches by swapping to cache with empty computed fields.

    Args:
        cache: The AppCache instance to clear. Defaults to the app_cache singleton.
    """
    # Import here to avoid circular imports
    from azurerbac.cache import app_cache

    if cache is None:
        cache = app_cache

    logger.debug("Clearing computed caches...")
    current = cache.cache

    # Create new cache with source data preserved, but empty computed fields
    new_cache = CacheData(
        all_operations=current.all_operations,
        roles_by_id=current.roles_by_id,
        all_change_events=current.all_change_events,
        operations_for_recommender=current.operations_for_recommender,
        unique_providers=current.unique_providers,
        last_scan=current.last_scan,
        first_scan=current.first_scan,
        ops_by_name_lower=current.ops_by_name_lower,
        ops_by_prefix=current.ops_by_prefix,
        # All computed fields default to empty
    )
    cache.swap(new_cache)
    logger.debug("Computed caches cleared")


def precompute_all_caches(
    roles: list[dict],
    all_operations: list[dict],
    cache: AppCache | None = None,
    *,
    metadata: CacheMetadata | None = None,
    roles_by_id: dict | None = None,
    all_change_events: list | None = None,
    last_scan: datetime | None = None,
    first_scan: datetime | None = None,
    swap_in_memory: bool = True,
) -> CacheData:
    """Pre-compute ALL caches at startup for maximum performance.

    This function should be called at startup and after any data changes.
    It precomputes:
    1. Common wildcard pattern matches (*/read, */write, etc.)
    2. All unique action patterns found in roles
    3. Role coverage data (which operations each role grants)
    4. Role net permission counts
    5. Prefix indexes for fast lookup

    Builds a complete CacheData with source data + computed fields.
    If swap_in_memory=True, atomically swaps via cache.swap().

    Args:
        roles: List of role JSON dicts from Role.role_json
        all_operations: List of operation dicts with 'name' and 'is_data_action' keys
        cache: The AppCache instance to populate. Defaults to the app_cache singleton.
        metadata: Optional CacheMetadata for versioning/invalidation.
        roles_by_id: Optional dict of roles by ID. If not provided, uses current cache.
        all_change_events: Optional list of change events. If not provided, uses current cache.
        last_scan: Optional timestamp. If not provided, uses current cache.
        first_scan: Optional timestamp. If not provided, uses current cache.
        swap_in_memory: If True, swap the new cache into memory. Default True.

    Returns:
        The new CacheData object (whether or not it was swapped in).
    """
    # Import here to avoid circular imports
    from azurerbac.cache import app_cache

    if cache is None:
        cache = app_cache

    start = time.time()
    logger.debug(
        "Precomputing caches for %d roles, %d operations...",
        len(roles),
        len(all_operations),
    )

    # Use provided source data or fall back to current cache
    current = cache.cache
    final_roles_by_id = roles_by_id if roles_by_id is not None else current.roles_by_id
    final_change_events = (
        all_change_events if all_change_events is not None else current.all_change_events
    )
    final_last_scan = last_scan if last_scan is not None else current.last_scan
    final_first_scan = first_scan if first_scan is not None else current.first_scan

    # Compute derived fields from all_operations
    operations_for_recommender = [
        {"name": op["name"], "is_data_action": op.get("is_data_action", False)}
        for op in all_operations
    ]
    providers: set[str] = {
        op["provider_display_name"] for op in all_operations if op.get("provider_display_name")
    }
    unique_providers = sorted(providers, key=str.casefold)

    # Build computed data into temporary dicts
    pattern_match: dict[tuple[str, int], set[str]] = {}
    wildcard_count: dict[tuple[str, int], int] = {}
    operations_by_prefix_computed: dict[int, dict[str, set[str]]] = {}
    role_coverage: dict[str, tuple[set[str], set[str]]] = {}
    role_net_permissions: dict[str, tuple[int, int]] = {}
    partial_coverage: dict[tuple, tuple[int, int, int, list[str]]] = {}

    # Separate control and data plane operations
    all_control_ops = {op["name"] for op in all_operations if not op.get("is_data_action")}
    all_data_ops = {op["name"] for op in all_operations if op.get("is_data_action")}

    logger.debug("Operations: %d control, %d data plane", len(all_control_ops), len(all_data_ops))

    # Build lowercase lookup sets for case-insensitive matching
    control_ops_lower_to_orig = {op.lower(): op for op in all_control_ops}
    data_ops_lower_to_orig = {op.lower(): op for op in all_data_ops}

    cache_ops_count = [len(all_control_ops), len(all_data_ops)]
    control_cache_key = len(all_control_ops)
    data_cache_key = len(all_data_ops) + 1000000

    # Build prefix indexes
    logger.debug("Building prefix indexes...")
    build_operations_prefix_index(all_control_ops, control_cache_key, operations_by_prefix_computed)
    build_operations_prefix_index(all_data_ops, data_cache_key, operations_by_prefix_computed)

    # 1. Precompute common patterns
    logger.debug("Precomputing common patterns...")
    _precompute_common_patterns(
        all_control_ops, all_data_ops, control_cache_key, data_cache_key, pattern_match
    )

    # 2. Collect and precompute all unique action patterns from roles
    logger.debug("Collecting unique action patterns from roles...")
    all_action_patterns = _collect_role_patterns(roles)
    logger.debug("Precomputing %d unique action patterns...", len(all_action_patterns))
    for pattern in all_action_patterns:
        get_matching_operations(pattern, all_control_ops, control_cache_key, pattern_match)
        get_matching_operations(pattern, all_data_ops, data_cache_key, pattern_match)

    # 3. Precompute role coverage and net permissions
    logger.debug("Computing role coverage and net permissions...")
    builtin_count = 0
    for role in roles:
        props = get_role_properties(role)
        role_id = get_role_id(role)
        if props.get("type", "") != "BuiltInRole":
            continue

        builtin_count += 1
        net_control, net_data = _compute_role_coverage(
            role,
            all_control_ops,
            all_data_ops,
            control_cache_key,
            data_cache_key,
            pattern_match,
            control_ops_lower_to_orig,
            data_ops_lower_to_orig,
        )
        role_coverage[role_id] = (net_control, net_data)
        role_net_permissions[role_id] = (len(net_control), len(net_data))

    logger.debug("Computed coverage for %d built-in roles", builtin_count)

    # Build operation -> role count from role coverage
    logger.debug("Building operation role count index...")
    operation_role_count = _build_operation_role_count(role_coverage)

    # Build operation indexes
    logger.debug("Building operation indexes...")
    ops_by_name_lower, ops_by_prefix = build_indexes(all_operations)

    # Create new unified cache with source data + computed fields
    new_cache = CacheData(
        # Metadata for versioning/invalidation
        metadata=metadata or CacheMetadata(),
        # Source data
        all_operations=all_operations,
        roles_by_id=final_roles_by_id,
        all_change_events=final_change_events,
        operations_for_recommender=operations_for_recommender,
        unique_providers=unique_providers,
        last_scan=final_last_scan,
        first_scan=final_first_scan,
        # Indexes
        ops_by_name_lower=ops_by_name_lower,
        ops_by_prefix=ops_by_prefix,
        # Computed fields
        role_coverage=role_coverage,
        role_net_permissions=role_net_permissions,
        operation_role_count=operation_role_count,
        pattern_match=pattern_match,
        partial_coverage=partial_coverage,
        wildcard_count=wildcard_count,
        operations_by_prefix_computed=operations_by_prefix_computed,
        cache_ops_count=cache_ops_count,
    )

    # Atomically swap the cache if requested
    if swap_in_memory:
        cache.swap(new_cache)

    elapsed = time.time() - start
    logger.info(
        f"Precomputed all caches in {elapsed:.2f}s: "
        f"{len(all_action_patterns)} patterns, {len(role_coverage)} roles, "
        f"{len(pattern_match)} pattern matches"
    )

    return new_cache
