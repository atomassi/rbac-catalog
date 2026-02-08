"""Pages service functions."""

from __future__ import annotations

import heapq
import json
import logging
from typing import TYPE_CHECKING, Any, Final

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE
from azurerbac.core.diffing import RoleDiff
from azurerbac.core.enums import SortOrder
from azurerbac.core.utils import truncate_microseconds
from azurerbac.web.services.models import (
    EnrichedChangeEvent,
    OperationSearchParams,
    OperationSortField,
    PermissionTimelinePoint,
    RawPermissions,
    RelatedRole,
    RoleAllowingOperation,
    RoleDetailResult,
    RoleEffectivePermissions,
    RolePermissionAnalyzer,
)
from azurerbac.web.utils import role_json_pretty

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from azurerbac.cache import CacheService
    from azurerbac.core.models import Role, RoleHistory

logger = logging.getLogger(__name__)


def filter_operations(
    operations: list[OperationData],
    params: OperationSearchParams,
) -> list[OperationData]:
    """Filter operations based on search parameters.

    Uses a single pass through operations to avoid creating multiple
    intermediate lists (reduces memory allocations and iterations).
    """
    # Pre-compute filter values
    q_lower = params.query.lower() if params.query else None
    filter_data_action = params.is_data_action
    filter_provider = params.provider

    # Single-pass filter combining all conditions
    return [
        op
        for op in operations
        if (q_lower is None or op.matches_search(q_lower))
        and (filter_data_action is None or op.is_data_action == filter_data_action)
        and (not filter_provider or op.provider_display_name == filter_provider)
    ]


# Sort key functions for OperationData objects
_OPERATION_SORT_KEYS: Final[dict[str | OperationSortField, Any]] = {
    OperationSortField.PROVIDER: lambda x: (x.provider_display_name or "").lower(),
    OperationSortField.TYPE: lambda x: x.is_data_action,
    OperationSortField.NAME: lambda x: x.name.lower(),
}


def sort_operations(
    operations: list[OperationData],
    sort: str | OperationSortField,
    order: str | SortOrder,
    cache: CacheService | None = None,
) -> list[OperationData]:
    """Sort operations.

    Role counts are NOT fetched here - callers should use add_role_counts()
    on the paginated subset to avoid 21k lookups when only displaying ~25 items.
    """
    reverse = order == SortOrder.DESC

    if sort == OperationSortField.ROLES:
        # Sort by role count - need to fetch counts for sorting
        cache_resolved = _get_cache(cache)
        return sorted(
            operations,
            key=lambda op: cache_resolved.get_operation_role_count(op.name),
            reverse=reverse,
        )

    # Sort by other fields - no role count lookup needed
    key_func = _OPERATION_SORT_KEYS.get(sort, _OPERATION_SORT_KEYS[OperationSortField.NAME])
    return sorted(operations, key=key_func, reverse=reverse)


def add_role_counts(
    operations: list[OperationData],
    cache: CacheService | None = None,
) -> list[tuple[OperationData, int]]:
    """Add role counts to operations. Call on paginated subset for efficiency."""
    cache_resolved = _get_cache(cache)
    return [(op, cache_resolved.get_operation_role_count(op.name)) for op in operations]


def compute_role_effective_permissions(
    role: RoleDefinition,
    all_operations: list[OperationData],
    cache: CacheService | None = None,
) -> RoleEffectivePermissions:
    """Compute effective permissions for a role."""
    analyzer = RolePermissionAnalyzer(role, cache=cache)
    return analyzer.get_effective_permissions(all_operations)


# Weights for composite related-role similarity
_W_OPS: Final[float] = 0.90
_W_SCOPE: Final[float] = 0.05
_W_COND: Final[float] = 0.05


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two frozensets."""
    if not a and not b:
        return 1.0
    union_size = len(a | b)
    return len(a & b) / union_size if union_size > 0 else 0.0


def _condition_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute condition similarity between two roles.

    Both roles unconditioned → 1.0 (equally unconstrained).
    One conditioned, one not → 0.0 (fundamentally different).
    Both conditioned → Jaccard on condition strings.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return _jaccard(a, b)


def _scopes_contain(broader: frozenset[str], narrower: frozenset[str]) -> bool:
    """Check if every scope in narrower is contained by at least one scope in broader.

    "/" (root) contains everything; otherwise uses case-insensitive prefix matching.
    """
    if broader == narrower:
        return True
    broader_lower = [s.lower().rstrip("/") for s in broader]
    for scope in narrower:
        scope_lower = scope.lower().rstrip("/")
        if not any(
            bs in ("", scope_lower) or scope_lower.startswith(bs + "/") for bs in broader_lower
        ):
            return False
    return True


def _extract_role_metadata(
    cached_role: CachedRole,
) -> tuple[frozenset[str], frozenset[str]]:
    """Extract assignable scopes and condition strings from a cached role."""
    props = cached_role.definition.properties
    scopes = frozenset(props.assignable_scopes) if props.assignable_scopes else frozenset(("/",))
    conditions = frozenset(p.condition for p in props.permissions if p.condition)
    return scopes, conditions


def compute_related_roles(
    role_id: str,
    limit: int = 12,
    cache: CacheService | None = None,
) -> list[RelatedRole]:
    """Compute roles with highest operation overlap using inverted index.

    Uses composite similarity: Jaccard overlap (90%), scope match (5%),
    condition similarity (5%).
    """
    from azurerbac.core.constants import RoleStatus

    if limit <= 0:
        return []

    cache_resolved = _get_cache(cache)

    # Check request cache first. Only use cache when it can satisfy the requested limit.
    cached_result = cache_resolved.get_related_roles(role_id)
    if cached_result is not None and len(cached_result) >= limit:
        return cached_result[:limit]

    current_cached = cache_resolved.get_role_by_id(role_id)
    if not current_cached:
        return []

    coverage = cache_resolved.get_role_coverage(role_id)
    if not coverage:
        return []

    # Separate control/data sets to avoid creating unions per candidate.
    # Since control and data ops are disjoint:
    #   |current ∩ other| = |current_ctrl ∩ other_ctrl| + |current_data ∩ other_data|
    current_control = coverage.control
    current_data = coverage.data
    current_len = len(current_control) + len(current_data)
    if current_len == 0:
        return []

    current_scopes, current_conditions = _extract_role_metadata(current_cached)

    # Use inverted index to count co-occurring roles efficiently
    co_occurrence: dict[str, int] = {}
    op_to_roles = cache_resolved.cache.operation_to_roles
    for ops_set in (current_control, current_data):
        for op in ops_set:
            for rid in op_to_roles.get(op, []):
                if rid != role_id:
                    co_occurrence[rid] = co_occurrence.get(rid, 0) + 1

    if not co_occurrence:
        return []

    # Take top candidates by co-occurrence (tie-break by role_id for determinism)
    top_candidates = heapq.nlargest(
        limit * 3,
        co_occurrence.items(),
        key=lambda x: (x[1], x[0]),
    )

    results: list[RelatedRole] = []
    for rid, _ in top_candidates:
        cached_role = cache_resolved.get_role_by_id(rid)
        if not cached_role or cached_role.status == RoleStatus.DELETED:
            continue

        other_coverage = cache_resolved.get_role_coverage(rid)
        if not other_coverage:
            continue

        # Compute intersection directly from separate sets (avoids set union per candidate)
        other_control_len = len(other_coverage.control)
        other_data_len = len(other_coverage.data)
        other_len = other_control_len + other_data_len
        if other_len == 0:
            continue

        intersection = len(current_control & other_coverage.control) + len(
            current_data & other_coverage.data
        )
        union = current_len + other_len - intersection
        ops_sim = intersection / union if union > 0 else 0.0

        other_scopes, other_conditions = _extract_role_metadata(cached_role)
        scope_sim = 1.0 if current_scopes == other_scopes else 0.0
        cond_sim = _condition_similarity(current_conditions, other_conditions)

        similarity = _W_OPS * ops_sim + _W_SCOPE * scope_sim + _W_COND * cond_sim

        if similarity < 0.20:
            continue

        # Subset/superset requires identical conditions
        same_conditions = current_conditions == other_conditions

        # other is a subset of current: all other ops in current, current scopes ⊇ other scopes
        ops_subset = intersection == other_len
        is_subset = same_conditions and ops_subset and _scopes_contain(current_scopes, other_scopes)

        # other is a superset of current: all current ops in other, other scopes ⊇ current scopes
        ops_superset = intersection == current_len
        is_superset = (
            same_conditions and ops_superset and _scopes_contain(other_scopes, current_scopes)
        )

        results.append(
            RelatedRole(
                role_id=rid,
                role_name=cached_role.role_name,
                similarity=similarity,
                shared_count=intersection,
                total_count=other_len,
                is_subset=is_subset,
                is_superset=is_superset,
            )
        )

    results.sort(key=lambda x: (-x.similarity, -x.shared_count, x.role_name))
    final = results[:limit]
    cache_resolved.set_related_roles(role_id, final)
    return final


def _get_cache(cache: CacheService | None) -> CacheService:
    """Get cache service."""
    if cache is not None:
        return cache
    from azurerbac.cache import get_cache_service

    return get_cache_service()


def get_roles_allowing_operation(
    operation_name: str,
    is_data_action: bool,
    cache: CacheService | None = None,
) -> list[RoleAllowingOperation]:
    """Find roles allowing a specific operation.

    Uses precomputed operation_to_roles index.
    """
    cache_resolved = _get_cache(cache)

    # Check cache first (key is just lowered operation name)
    cache_key = operation_name.lower()
    if (cached := cache_resolved.get_allowing_roles(cache_key)) is not None:
        logger.debug("allowing_roles cache hit for %s", operation_name)
        return cached

    # lookup using precomputed inverted index
    if not (role_ids := cache_resolved.get_roles_for_operation(cache_key)):
        cache_resolved.set_allowing_roles(cache_key, [])
        return []

    logger.debug(
        "allowing_roles cache miss for %s, found %d role IDs in index",
        operation_name,
        len(role_ids),
    )

    allowing_roles: list[RoleAllowingOperation] = []

    for role_id in role_ids:
        if (cached_role := cache_resolved.get_role_by_id(role_id)) is None:
            msg = f"Cache inconsistency: role {role_id} in index but not in cache"
            raise RuntimeError(msg)

        # Get precomputed net permissions (always available for indexed roles)
        if (net_perms := cache_resolved.get_role_net_permissions(role_id)) is None:
            msg = f"Cache inconsistency: role {role_id} in index but missing net_perms"
            raise RuntimeError(msg)

        role = cached_role.definition

        analyzer = RolePermissionAnalyzer(role, cache=cache_resolved)
        match_result = analyzer.find_matching_pattern(operation_name, is_data_action=is_data_action)

        allowing_roles.append(
            RoleAllowingOperation.from_match(
                role_id=role_id,
                role_name=role.properties.role_name,
                role_type=role.properties.type or DEFAULT_ROLE_TYPE,
                control_count=net_perms.control_count,
                data_count=net_perms.data_count,
                match_result=match_result,
            )
        )

    allowing_roles.sort(key=lambda x: x.role_name.lower())

    cache_resolved.set_allowing_roles(cache_key, allowing_roles)
    logger.debug(
        "allowing_roles computed: %d roles allow %s, cached as %s",
        len(allowing_roles),
        operation_name,
        cache_key,
    )

    return allowing_roles


async def get_role_from_cache_or_db(
    session_local: async_sessionmaker,
    role_snapshot_model: type[Role],
    role_history_model: type[RoleHistory],
    role_id: str,
    max_events: int = 200,
    *,
    cache: CacheService | None = None,
) -> RoleDetailResult:
    """Get role data from cache or database."""
    from sqlalchemy import func, select

    from azurerbac.telemetry import TimedDbQuery

    cache_resolved = _get_cache(cache)

    cached_role = cache_resolved.get_role_by_id(role_id)

    if cached_role:
        first_scan = cache_resolved.cache.first_scan
        cached_events = cache_resolved.get_events_for_role(role_id)
        events_raw = cached_events[:max_events]
        return RoleDetailResult(
            cached_role=cached_role,
            definition=cached_role.definition,
            events=events_raw,
            first_scan=first_scan,
        )

    # Cache miss - fall back to database (tracked via TimedDbQuery)
    async with session_local() as session:
        async with TimedDbQuery("fetch_role_by_id", fallback_type="role_detail") as timer:
            role = await session.get(role_snapshot_model, role_id)
            timer.rows = 1 if role else 0
        if role is None:
            return RoleDetailResult(cached_role=None, definition=None, events=[], first_scan=None)
        role_def = role.last_known_definition

        # Build CachedRole from database model
        cached_role_from_db = (
            CachedRole(
                definition=role_def,
                status=role.status,
                last_seen_at=role.last_seen_at,
            )
            if role_def
            else None
        )

        # Get first scan timestamp from RoleScanStatus
        from azurerbac.core import RoleScanStatus

        async with TimedDbQuery("fetch_first_scan_timestamp", fallback_type="role_detail"):
            first_scan = await session.scalar(select(func.min(RoleScanStatus.scan_timestamp)))
        first_scan = truncate_microseconds(first_scan)

        # Get history entries for this role - RoleHistory has role_id directly
        async with TimedDbQuery("fetch_role_history", fallback_type="role_detail") as timer:
            events_result = await session.execute(
                select(role_history_model)
                .where(role_history_model.role_id == role_id)
                .order_by(role_history_model.scan_id.desc())
                .limit(max_events)
            )
            db_events = events_result.scalars().all()
            timer.rows = len(db_events)
        events_raw = [
            CachedChangeEvent(
                id=ev.id,
                role_id=ev.role_id,
                role_name=ev.role_name,
                event_type=ev.event_type,
                scan_timestamp=ev.scan.scan_timestamp if ev.scan else None,
                azure_updated_on=ev.azure_updated_on,
                summary=ev.summary,
                diff_json=ev.diff_json,
                role_json=ev.role_json,
            )
            for ev in db_events
        ]
        return RoleDetailResult(
            cached_role=cached_role_from_db,
            definition=role_def,
            events=events_raw,
            first_scan=first_scan,
        )


def build_role_redirect_url(
    request: Request,
    role_id: str,
    expected_slug: str,
    q: str | None,
    page: int,
    limit: int,
    days: int,
) -> str:
    """Build redirect URL with canonical slug for role detail page."""
    from urllib.parse import urlencode

    from azurerbac.web.constants import DEFAULT_DAYS, DEFAULT_LIMIT, DEFAULT_PAGE

    # Build params dict, omitting defaults
    query_params: dict[str, str] = {}
    if q:
        query_params["q"] = q
    if page != DEFAULT_PAGE:
        query_params["page"] = str(page)
    if limit != DEFAULT_LIMIT:
        query_params["limit"] = str(limit)
    if days != DEFAULT_DAYS:
        query_params["days"] = str(days)

    if expected_slug:
        url = request.url_for("role_detail_slug", role_id=role_id, slug=expected_slug)
    else:
        url = request.url_for("role_detail", role_id=role_id)

    if query_params:
        url = f"{url}?{urlencode(query_params)}"
    return str(url)


def _count_permission_patterns(role_json: dict) -> tuple[int, int]:
    """Count action and data action patterns from a role_json dict.

    Returns:
        Tuple of (actions_count, data_actions_count).
    """
    props = role_json.get("properties", {})
    permissions = props.get("permissions", [])
    actions = 0
    data_actions = 0
    for perm in permissions:
        if isinstance(perm, dict):
            actions += len(perm.get("actions", []))
            data_actions += len(perm.get("dataActions", []))
    return actions, data_actions


_EVENT_LABELS: Final[dict[str, str]] = {
    "created": "Created",
    "initial_scan": "Initial scan",
    "updated": "Updated",
}


def _compute_effective_counts(
    role_json: dict,
    all_control_ops: set[str],
    all_data_ops: set[str],
) -> tuple[int, int]:
    """Compute effective (expanded) permission counts for a historical role version.

    Parses role_json into a RoleDefinition, then uses RawPermissions to expand
    wildcards and apply NotActions/NotDataActions subtraction.

    Args:
        role_json: Historical role JSON from RoleHistory.
        all_control_ops: Lowered set of all known control-plane operations.
        all_data_ops: Lowered set of all known data-plane operations.

    Returns:
        Tuple of (effective_control_count, effective_data_count).
    """
    role_def = RoleDefinition.model_validate(role_json)
    raw = RawPermissions.from_permissions(role_def.properties.permissions)
    coverage = raw.compute_effective(all_control_ops, all_data_ops)
    return len(coverage.control), len(coverage.data)


def build_permission_timeline(
    events: list[CachedChangeEvent],
    all_operations: list[OperationData] | None = None,
) -> list[PermissionTimelinePoint]:
    """Build a permission timeline from role history events.

    Walks the event list (newest-first) and extracts both raw pattern counts
    and effective (wildcard-expanded) permission counts from role_json at each
    version. Returns points in chronological order (oldest first) for charting.

    Args:
        events: Role change events, ordered newest-first.
        all_operations: All known operations for wildcard expansion.
            When None, effective counts fall back to pattern counts.

    Returns:
        List of timeline points in chronological order.
    """
    # Pre-compute operation sets once for all versions
    all_control_ops: set[str] = set()
    all_data_ops: set[str] = set()
    if all_operations:
        all_control_ops = {op.name.lower() for op in all_operations if not op.is_data_action}
        all_data_ops = {op.name.lower() for op in all_operations if op.is_data_action}

    points: list[PermissionTimelinePoint] = []

    for ev in reversed(events):
        # Skip events without role JSON (e.g. delete events)
        if not ev.role_json:
            # For delete events, add a zero-point to show the drop-off
            if ev.event_type == "deleted":
                ts = ev.scan_timestamp or ev.azure_updated_on
                if ts:
                    points.append(
                        PermissionTimelinePoint(
                            date=ts.strftime("%Y-%m-%d"),
                            version=0,
                            event_type=ev.event_type,
                            actions=0,
                            data_actions=0,
                            total=0,
                            effective_actions=0,
                            effective_data_actions=0,
                            effective_total=0,
                            label="Deleted",
                        )
                    )
            continue

        ts = ev.azure_updated_on or ev.scan_timestamp
        if ts is None:
            continue

        actions, data_actions = _count_permission_patterns(ev.role_json)
        total = actions + data_actions

        # Compute effective (expanded) counts when operations are available
        if all_control_ops or all_data_ops:
            eff_actions, eff_data = _compute_effective_counts(
                ev.role_json, all_control_ops, all_data_ops
            )
        else:
            eff_actions, eff_data = actions, data_actions

        label = _EVENT_LABELS.get(ev.event_type, ev.event_type.replace("_", " ").title())

        points.append(
            PermissionTimelinePoint(
                date=ts.strftime("%Y-%m-%d"),
                version=len(points) + 1,
                event_type=ev.event_type,
                actions=actions,
                data_actions=data_actions,
                total=total,
                effective_actions=eff_actions,
                effective_data_actions=eff_data,
                effective_total=eff_actions + eff_data,
                label=label,
            )
        )

    return points


def enrich_event_with_diff(ev: CachedChangeEvent) -> EnrichedChangeEvent:
    """Enrich a role change event with processed diff_json."""
    diff: RoleDiff | None = RoleDiff.from_dict(ev.diff_json)

    # Only process before_json/after_json if diff exists and at least one side is present
    # (delete events only have "changes", not the full JSON)
    if diff is not None and (diff.before_json is not None or diff.after_json is not None):
        before_role = RoleDefinition.model_validate(diff.before_json) if diff.before_json else None
        after_role = RoleDefinition.model_validate(diff.after_json) if diff.after_json else None

        # Normalize createdOn to avoid showing it as a diff
        created_on = next(
            (r.properties.created_on for r in (after_role, before_role) if r),
            None,
        )

        for role in (before_role, after_role):
            if role and created_on:
                role.properties.created_on = created_on

        diff = RoleDiff(
            changed=diff.changed,
            changes=diff.changes,
            before_json=before_role.to_dict() if before_role else None,
            after_json=after_role.to_dict() if after_role else None,
        )

    # Process role_json for created/initial_scan events
    role_json_pretty_str = ""
    if (role_json := ev.role_json) and role_json:
        parsed = RoleDefinition.model_validate(role_json)
        role_json_pretty_str = role_json_pretty(parsed.to_dict())

    diff_dict = diff.to_dict() if diff else None
    return EnrichedChangeEvent(
        scan_timestamp=ev.scan_timestamp,
        azure_updated_on=ev.azure_updated_on,
        event_type=ev.event_type,
        summary=ev.summary,
        diff=diff,
        diff_pretty=(json.dumps(diff_dict, indent=2, default=str) if diff_dict else ""),
        role_json_pretty=role_json_pretty_str,
    )
