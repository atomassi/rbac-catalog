"""Cache build and computation functions."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import (
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    PatternCacheKey,
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.core.constants import RoleStatus
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern
from azurerbac.core.utils import truncate_microseconds
from azurerbac.matching.models import (
    CacheOpsCount,
    CoverageResult,
    PartialCoverageCacheKey,
    Plane,
    RoleCoverage,
    RoleNetPermissions,
)

if TYPE_CHECKING:
    from azurerbac.analytics.models import AnalyticsData

logger = logging.getLogger(__name__)


def get_matching_operations(
    pattern: str,
    ops: set[str],
    plane: Plane,
    pattern_cache: dict[PatternCacheKey, set[str]],
) -> set[str]:
    """Get operations matching a pattern (returns lowered)."""
    key = PatternCacheKey(pattern.lower(), plane)
    if key in pattern_cache:
        return pattern_cache[key]

    matching = {op.lower() for op in ops if matches_pattern(op, pattern)}
    pattern_cache[key] = matching
    return matching


def build_operations_prefix_index(
    ops: set[str],
    plane: Plane,
    prefix_cache: dict[Plane, dict[str, set[str]]],
) -> dict[str, set[str]]:
    """Build index of operations by provider prefix."""
    if plane in prefix_cache:
        return prefix_cache[plane]

    index: dict[str, set[str]] = {}
    for op in ops:
        slash_idx = op.find("/")
        if slash_idx > 0:
            prefix = op[: slash_idx + 1].lower()
            index.setdefault(prefix, set()).add(op)

    prefix_cache[plane] = index
    return index


def _add_operations_for_patterns(
    dst: set[str],
    patterns: list[str],
    *,
    all_ops: set[str],
    plane: Plane,
    pattern_match: dict[PatternCacheKey, set[str]],
    ops_lower_to_orig: dict[str, str],
) -> None:
    """Add operations matching patterns to destination set (lowered)."""
    for pattern in patterns:
        if pattern == "*":
            dst.update(ops_lower_to_orig.keys())
        elif is_wildcard_pattern(pattern):
            dst.update(get_matching_operations(pattern, all_ops, plane, pattern_match))
        else:
            pattern_lower = pattern.lower()
            if pattern_lower in ops_lower_to_orig:
                dst.add(pattern_lower)


def _precompute_common_patterns(
    all_control_ops: set[str],
    all_data_ops: set[str],
    pattern_match: dict[PatternCacheKey, set[str]],
) -> None:
    """Precompute common wildcard patterns."""
    common_patterns = ["*/read", "*/write", "*/delete", "*/action", "*/listkeys/action", "*"]
    for pattern in common_patterns:
        get_matching_operations(pattern, all_control_ops, Plane.CONTROL, pattern_match)
        get_matching_operations(pattern, all_data_ops, Plane.DATA, pattern_match)


def _collect_role_patterns(roles: list[RoleDefinition]) -> set[str]:
    """Collect all unique wildcard action patterns from roles."""
    patterns: set[str] = set()
    for role in roles:
        for perm in role.properties.permissions:
            all_actions = (
                perm.actions + perm.not_actions + perm.data_actions + perm.not_data_actions
            )
            for action in all_actions:
                if is_wildcard_pattern(action):
                    patterns.add(action.lower())
    return patterns


def _compute_role_coverage(
    role: RoleDefinition,
    all_control_ops: set[str],
    all_data_ops: set[str],
    pattern_match: dict[PatternCacheKey, set[str]],
    control_ops_lower_to_orig: dict[str, str],
    data_ops_lower_to_orig: dict[str, str],
) -> RoleCoverage:
    """Compute effective operations (granted - excluded) for a role."""
    control_granted: set[str] = set()
    data_granted: set[str] = set()
    control_excluded: set[str] = set()
    data_excluded: set[str] = set()

    for perm in role.properties.permissions:
        _add_operations_for_patterns(
            control_granted,
            perm.actions,
            all_ops=all_control_ops,
            plane=Plane.CONTROL,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            control_excluded,
            perm.not_actions,
            all_ops=all_control_ops,
            plane=Plane.CONTROL,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_granted,
            perm.data_actions,
            all_ops=all_data_ops,
            plane=Plane.DATA,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_excluded,
            perm.not_data_actions,
            all_ops=all_data_ops,
            plane=Plane.DATA,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )

    return RoleCoverage(control_granted - control_excluded, data_granted - data_excluded)


def _build_operation_role_count(
    role_coverage: dict[str, RoleCoverage],
) -> dict[str, int]:
    """Build operation -> role count index from role coverage."""
    counts: dict[str, int] = {}
    for cov in role_coverage.values():
        for op in cov.control:
            counts[op] = counts.get(op, 0) + 1
        for op in cov.data:
            counts[op] = counts.get(op, 0) + 1
    return counts


def precompute_all(
    roles: list[RoleDefinition],
    all_operations: list[OperationData],
    *,
    metadata: CacheMetadata | None = None,
    roles_by_id: dict[str, CachedRole] | None = None,
    all_change_events: list[CachedChangeEvent] | None = None,
    last_scan: datetime | None = None,
    first_scan: datetime | None = None,
    analytics: AnalyticsData | None = None,
) -> CacheData:
    """Pre-compute ALL caches and return complete CacheData.

    Precomputes:
    1. Common wildcard pattern matches (*/read, */write, etc.)
    2. All unique action patterns found in roles
    3. Role coverage data (which operations each role grants)
    4. Role net permission counts
    5. Prefix indexes for fast lookup

    Args:
        roles: List of RoleDefinition Pydantic models
        all_operations: List of OperationData models
        metadata: Optional CacheMetadata for versioning/invalidation.
        roles_by_id: Optional dict of roles by ID.
        all_change_events: Optional list of change events.
        last_scan: Optional timestamp.
        first_scan: Optional timestamp.
        analytics: Optional pre-computed analytics data.

    Returns:
        Complete CacheData with all computed fields.
    """
    start = time.time()
    logger.debug(
        "Precomputing caches for %d roles, %d operations...",
        len(roles),
        len(all_operations),
    )

    providers: set[str] = {
        op.provider_display_name for op in all_operations if op.provider_display_name
    }
    unique_providers = sorted(providers, key=str.lower)

    # Build computed data into temporary dicts
    pattern_match: dict[PatternCacheKey, set[str]] = {}
    wildcard_count: dict[PatternCacheKey, int] = {}
    operations_by_prefix_computed: dict[Plane, dict[str, set[str]]] = {}
    role_coverage: dict[str, RoleCoverage] = {}
    role_net_permissions: dict[str, RoleNetPermissions] = {}
    partial_coverage: dict[PartialCoverageCacheKey, CoverageResult] = {}

    # Separate control and data plane operations
    all_control_ops = {op.name for op in all_operations if not op.is_data_action}
    all_data_ops = {op.name for op in all_operations if op.is_data_action}

    logger.debug("Operations: %d control, %d data plane", len(all_control_ops), len(all_data_ops))

    # Build lowered lookup sets for case-insensitive matching
    control_ops_lower_to_orig = {op.lower(): op for op in all_control_ops}
    data_ops_lower_to_orig = {op.lower(): op for op in all_data_ops}

    cache_ops_count = CacheOpsCount(len(all_control_ops), len(all_data_ops))

    # Build prefix indexes
    logger.debug("Building prefix indexes...")
    build_operations_prefix_index(all_control_ops, Plane.CONTROL, operations_by_prefix_computed)
    build_operations_prefix_index(all_data_ops, Plane.DATA, operations_by_prefix_computed)

    # 1. Precompute common patterns
    logger.debug("Precomputing common patterns...")
    _precompute_common_patterns(all_control_ops, all_data_ops, pattern_match)

    # 2. Collect and precompute all unique action patterns from roles
    logger.debug("Collecting unique action patterns from roles...")
    all_action_patterns = _collect_role_patterns(roles)
    logger.debug("Precomputing %d unique action patterns...", len(all_action_patterns))
    for pattern in all_action_patterns:
        get_matching_operations(pattern, all_control_ops, Plane.CONTROL, pattern_match)
        get_matching_operations(pattern, all_data_ops, Plane.DATA, pattern_match)

    # 3. Precompute role coverage and net permissions
    logger.debug("Computing role coverage and net permissions...")
    builtin_count = 0
    for role in roles:
        if not role.is_builtin:
            continue

        builtin_count += 1
        coverage = _compute_role_coverage(
            role,
            all_control_ops,
            all_data_ops,
            pattern_match,
            control_ops_lower_to_orig,
            data_ops_lower_to_orig,
        )
        role_coverage[role.role_id] = coverage
        role_net_permissions[role.role_id] = RoleNetPermissions(
            len(coverage.control), len(coverage.data)
        )

    logger.debug("Computed coverage for %d built-in roles", builtin_count)

    # Build operation -> role count from role coverage
    logger.debug("Building operation role count index...")
    operation_role_count = _build_operation_role_count(role_coverage)

    # Build operation indexes
    logger.debug("Building operation indexes...")
    ops_by_name_lower, ops_by_prefix = build_indexes(all_operations)

    # Create complete cache with source data + computed fields
    new_cache = CacheData(
        metadata=metadata or CacheMetadata(),
        all_operations=all_operations,
        roles_by_id=roles_by_id or {},
        all_change_events=all_change_events or [],
        unique_providers=unique_providers,
        last_scan=last_scan,
        first_scan=first_scan,
        ops_by_name_lower=ops_by_name_lower,
        ops_by_prefix=ops_by_prefix,
        role_coverage=role_coverage,
        role_net_permissions=role_net_permissions,
        operation_role_count=operation_role_count,
        pattern_match=pattern_match,
        partial_coverage=partial_coverage,
        wildcard_count=wildcard_count,
        operations_by_prefix_computed=operations_by_prefix_computed,
        cache_ops_count=cache_ops_count,
        analytics=analytics,
    )

    elapsed = time.time() - start
    logger.info(
        f"Precomputed all caches in {elapsed:.2f}s: "
        f"{len(all_action_patterns)} patterns, {len(role_coverage)} roles, "
        f"{len(pattern_match)} pattern matches"
    )

    return new_cache


async def build_from_db(session: AsyncSession) -> CacheData:
    """Build cache data from database."""
    from sqlalchemy import func, select

    from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus
    from azurerbac.telemetry import TimedDbQuery

    logger.info("Building cache from database...")

    # Fetch ALL roles (active and deleted) for roles_by_id index
    async with TimedDbQuery("fetch_all_roles") as timer:
        all_roles_result = await session.execute(select(Role))
        all_role_snapshots = list(all_roles_result.scalars().all())
        timer.rows = len(all_role_snapshots)

    # Fetch active roles for role_jsons (used by recommender)
    active_roles = [r for r in all_role_snapshots if r.status == RoleStatus.ACTIVE]
    logger.debug(f"Found {len(active_roles)} active roles out of {len(all_role_snapshots)} total")

    # Fetch all operations
    async with TimedDbQuery("fetch_all_operations") as timer:
        ops_result = await session.execute(select(Operation))
        all_ops = list(ops_result.scalars().all())
        timer.rows = len(all_ops)

    # Fetch all history events
    async with TimedDbQuery("fetch_all_history_events") as timer:
        events_result = await session.execute(
            select(RoleHistory).order_by(RoleHistory.scan_id.desc())
        )
        all_events = list(events_result.scalars().all())
        timer.rows = len(all_events)

    # Get scan status
    async with TimedDbQuery("fetch_scan_status"):
        last_scan = await session.scalar(
            select(RoleScanStatus.scan_timestamp)
            .order_by(RoleScanStatus.scan_timestamp.desc())
            .limit(1)
        )
        first_scan = await session.scalar(select(func.min(RoleScanStatus.scan_timestamp)))

    # Collect RoleDefinition objects
    role_definitions = [role.role_definition for role in active_roles if role.role_definition]

    # Convert DB Operation models to OperationData Pydantic models
    all_operations = [
        OperationData.model_validate(
            {
                "name": op.name,
                "displayName": op.display_name,
                "description": op.description,
                "provider_display_name": op.provider_display_name or "",
                "resource_type_display_name": op.resource_type_display_name,
                "isDataAction": op.is_data_action,
            }
        )
        for op in all_ops
    ]

    # Build roles_by_id index with CachedRole objects
    roles_by_id: dict[str, CachedRole] = {}
    for role in all_role_snapshots:
        role_def = role.last_known_definition
        if role_def:
            roles_by_id[role.role_id] = CachedRole(
                definition=role_def,
                status=role.status,
                last_seen_at=role.last_seen_at,
            )

    # Build change events list
    all_change_events: list[CachedChangeEvent] = []
    for ev in all_events:
        cached_role = roles_by_id.get(ev.role_id)
        role_name = cached_role.role_name if cached_role else ev.role_name
        all_change_events.append(
            CachedChangeEvent(
                id=ev.id,
                role_id=ev.role_id,
                role_name=role_name,
                event_type=ev.event_type,
                scan_timestamp=ev.scan.scan_timestamp if ev.scan else None,
                azure_updated_on=ev.azure_updated_on,
                summary=ev.summary,
                diff_json=ev.diff_json,
                role_json=ev.role_definition.to_dict() if ev.role_definition else None,
            )
        )

    # Compute hashes
    roles_hash = compute_roles_hash(role_definitions)
    operations_hash = compute_operations_hash(all_operations)

    # Create metadata
    metadata = CacheMetadata(
        roles_count=len(active_roles),
        operations_count=len(all_operations),
        roles_hash=roles_hash,
        operations_hash=operations_hash,
    )

    # Precompute all caches (without analytics yet)
    cache_data = precompute_all(
        role_definitions,
        all_operations,
        metadata=metadata,
        roles_by_id=roles_by_id,
        all_change_events=all_change_events,
        last_scan=truncate_microseconds(last_scan),
        first_scan=truncate_microseconds(first_scan),
    )

    # Build analytics data (now we have role_net_permissions available)
    from azurerbac.analytics.service import AnalyticsService

    analytics_service = AnalyticsService()
    all_ops_lower = {op.name.lower() for op in all_operations}
    analytics_data = await analytics_service.build_from_db(
        session,
        all_ops_lower,
        roles_by_id=roles_by_id,
        role_net_permissions=cache_data.role_net_permissions,
    )
    cache_data.analytics = analytics_data

    logger.info(
        f"Cache built: {len(active_roles)} roles, {len(all_operations)} operations, "
        f"{len(roles_by_id)} indexed, {len(all_change_events)} events"
    )

    return cache_data
