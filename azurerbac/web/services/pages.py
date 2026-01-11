"""Pages service functions for the Azure RBAC Catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from azurerbac.azure.models import OperationData, Permission, RoleDefinition
from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern
from azurerbac.web.services.dashboard import SortOrder
from azurerbac.web.services.models import (
    EnrichedChangeEvent,
    PatternMatchResult,
    RoleAllowingOperation,
    RoleDetailResult,
    RoleEffectivePermissions,
)

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from azurerbac.cache import CacheContainer
    from azurerbac.core.models import Role, RoleHistory

# =============================================================================
# Operation Search/Filter Types
# =============================================================================


class OperationSortField(StrEnum):
    """Valid sort fields for operation listings."""

    NAME = "name"
    PROVIDER = "provider"
    TYPE = "type"
    ROLES = "roles"


class DataActionFilter(StrEnum):
    """Filter for data vs control plane actions.

    Maps query string values to boolean filters:
    - "1" -> True (data plane only)
    - "0" -> False (control plane only)
    - None/other -> all actions
    """

    DATA = "1"
    CONTROL = "0"

    @classmethod
    def parse(cls, value: str | None) -> bool | None:
        """Parse a query string value to a boolean filter.

        Args:
            value: Query string value ("1", "0", or None)

        Returns:
            True for data actions, False for control actions, None for all.
        """
        if value == cls.DATA:
            return True
        if value == cls.CONTROL:
            return False
        return None


@dataclass(frozen=True, slots=True)
class OperationSearchParams:
    """Parameters for operation search and filtering.

    Encapsulates all filter/sort parameters that often travel together.
    """

    query: str | None = None
    is_data_action: bool | None = None  # None = all, True = data, False = control
    provider: str | None = None
    sort: str | OperationSortField = OperationSortField.NAME
    order: str | SortOrder = SortOrder.ASC


def operation_matches_search(op: OperationData, query_lower: str) -> bool:
    """Check if an OperationData matches a text search query.

    Searches across name, display_name, description, provider, and resource type.
    """
    return (
        query_lower in op.name.lower()
        or query_lower in (op.display_name or "").lower()
        or query_lower in (op.description or "").lower()
        or query_lower in (op.provider_display_name or "").lower()
        or query_lower in (op.resource_type_display_name or "").lower()
    )


def filter_operations(
    operations: list[OperationData],
    params: OperationSearchParams,
) -> list[OperationData]:
    """Filter operations based on search parameters.

    Args:
        operations: List of OperationData objects to filter
        params: Search/filter parameters

    Returns:
        Filtered list of operations (original order preserved)
    """
    result = operations

    # Apply text search
    if params.query:
        q_lower = params.query.lower()
        result = [op for op in result if operation_matches_search(op, q_lower)]

    # Apply data action filter
    if params.is_data_action is not None:
        result = [op for op in result if op.is_data_action == params.is_data_action]

    # Apply provider filter
    if params.provider:
        result = [op for op in result if op.provider_display_name == params.provider]

    return result


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
    cache: CacheContainer,
) -> list[tuple[OperationData, int]]:
    """Sort operations based on sort field and order.

    Args:
        operations: List of OperationData objects to sort
        sort: Sort field - OperationSortField enum or string
        order: Sort order - SortOrder enum or string
        cache: The cache container instance

    Returns:
        List of (operation, role_count) tuples, sorted as requested
    """
    # Build tuples with role count for sorting (needed for "roles" sort and enrichment)
    ops_with_count = [(op, cache.get_operation_role_count(op.name)) for op in operations]

    if sort == OperationSortField.ROLES:
        # Sort by role count
        ops_with_count.sort(key=lambda x: x[1], reverse=(order == SortOrder.DESC))
    else:
        key_func = _OPERATION_SORT_KEYS.get(sort, _OPERATION_SORT_KEYS[OperationSortField.NAME])
        ops_with_count.sort(key=lambda x: key_func(x[0]), reverse=(order == SortOrder.DESC))

    return ops_with_count


# =============================================================================
# Role Permissions
# =============================================================================


def _expand_patterns_to_operations(patterns: list[str], all_ops: set[str]) -> set[str]:
    """Expand permission patterns (including wildcards) to actual operations.

    Args:
        patterns: List of permission patterns (may include wildcards like *)
        all_ops: Set of all known operation names to match against

    Returns:
        Set of operation names that match the patterns
    """
    result: set[str] = set()
    for pattern in patterns:
        if pattern == "*":
            result.update(all_ops)
        elif is_wildcard_pattern(pattern):
            for op in all_ops:
                if matches_pattern(op, pattern):
                    result.add(op)
        elif pattern in all_ops:
            result.add(pattern)
    return result


@dataclass(frozen=True, slots=True)
class RawPermissions:
    """Raw permission patterns extracted from a role definition."""

    actions: list[str]
    not_actions: list[str]
    data_actions: list[str]
    not_data_actions: list[str]


def _extract_raw_permissions(permissions: list[Permission]) -> RawPermissions:
    """Extract raw permission patterns from role permissions.

    Args:
        permissions: List of Permission objects from role definition

    Returns:
        RawPermissions containing all action patterns
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
    return RawPermissions(
        actions=actions,
        not_actions=not_actions,
        data_actions=data_actions,
        not_data_actions=not_data_actions,
    )


def compute_role_effective_permissions(
    role: RoleDefinition, all_operations: list[OperationData], cache: CacheContainer
) -> RoleEffectivePermissions:
    """Compute the effective permissions for a role.

    Applies the same logic as the recommender:
    - actions - notActions for control plane
    - dataActions - notDataActions for data plane

    Args:
        role: The RoleDefinition Pydantic model.
        all_operations: List of all known Azure operations.
        cache: The cache container instance.

    Returns:
        RoleEffectivePermissions with control/data plane actions and metadata.
    """
    role_id = role.name
    permissions = role.properties.permissions

    # Extract raw patterns from the role
    raw = _extract_raw_permissions(permissions)

    # Check for ABAC conditions
    has_conditions = any(perm.condition for perm in permissions)

    # Try to get from cache first (already computed during startup)
    if cached_coverage := cache.get_role_coverage(role_id):
        control_effective, data_effective = cached_coverage
    else:
        # Fallback: compute manually (shouldn't happen if precompute_all_caches ran)
        all_control_ops = {op.name for op in all_operations if not op.is_data_action}
        all_data_ops = {op.name for op in all_operations if op.is_data_action}

        # Calculate effective permissions
        control_granted = _expand_patterns_to_operations(raw.actions, all_control_ops)
        control_excluded = _expand_patterns_to_operations(raw.not_actions, all_control_ops)
        control_effective = control_granted - control_excluded

        data_granted = _expand_patterns_to_operations(raw.data_actions, all_data_ops)
        data_excluded = _expand_patterns_to_operations(raw.not_data_actions, all_data_ops)
        data_effective = data_granted - data_excluded

    # Check if any patterns contain wildcards
    all_patterns = raw.actions + raw.not_actions + raw.data_actions + raw.not_data_actions
    has_wildcards = any(is_wildcard_pattern(p) or p == "*" for p in all_patterns)

    # Detect if role has defined permissions but none could be resolved
    has_defined_permissions = bool(raw.actions or raw.data_actions)
    has_resolved_operations = bool(control_effective or data_effective)
    has_unresolved_permissions = has_defined_permissions and not has_resolved_operations

    return RoleEffectivePermissions(
        control_plane_actions=sorted(control_effective),
        data_plane_actions=sorted(data_effective),
        control_plane_count=len(control_effective),
        data_plane_count=len(data_effective),
        has_conditions=has_conditions,
        has_wildcards=has_wildcards,
        has_unresolved_permissions=has_unresolved_permissions,
        raw_actions=raw.actions,
        raw_not_actions=raw.not_actions,
        raw_data_actions=raw.data_actions,
        raw_not_data_actions=raw.not_data_actions,
    )


def _find_matching_pattern_and_condition(
    operation_name: str,
    operation_lower: str,
    is_data_action: bool,
    permissions: list[Permission],
) -> PatternMatchResult:
    """Find the pattern that matches the operation and check for conditions.

    Returns:
        PatternMatchResult with matched pattern, condition flag, and condition text.
    """
    for perm in permissions:
        actions = perm.data_actions if is_data_action else perm.actions
        for pattern in actions:
            if matches_pattern(operation_name, pattern):
                condition = perm.condition or ""
                has_condition = bool(condition and operation_lower in condition.lower())
                return PatternMatchResult(
                    matched_pattern=pattern,
                    has_condition=has_condition,
                    condition_text=condition if has_condition else None,
                )
    return PatternMatchResult(matched_pattern=None, has_condition=False, condition_text=None)


def _operation_in_set(operation_lower: str, operation_set: set[str]) -> bool:
    """Check if operation is in the effective set (case-insensitive)."""
    return any(op.lower() == operation_lower for op in operation_set)


def get_roles_allowing_operation(
    operation_name: str, is_data_action: bool, cache: CacheContainer
) -> list[RoleAllowingOperation]:
    """Find all roles that allow a specific operation.

    Uses the pre-computed role coverage cache from the recommender.
    This cache contains the expanded set of actual operations each role covers,
    after applying notActions/notDataActions exclusions.

    Args:
        operation_name: The name of the operation to search for.
        is_data_action: Whether this is a data action (vs control plane action).
        cache: The cache container instance.

    Returns:
        List of RoleAllowingOperation with role details and matched pattern.
    """
    # Check cache first
    cache_key = f"roles_allowing_op:{operation_name.lower()}:{is_data_action}"
    if (cached := cache.get(cache_key)) is not None:
        return cached

    if not (all_roles := cache.get_all_roles()):
        return []

    allowing_roles: list[RoleAllowingOperation] = []
    operation_lower = operation_name.lower()

    for role in all_roles:
        role_id = role.name
        cached_coverage = cache.get_role_coverage(role_id)
        if not cached_coverage:
            continue

        control_effective, data_effective = cached_coverage
        operation_set = data_effective if is_data_action else control_effective

        if not _operation_in_set(operation_lower, operation_set):
            continue

        permissions = role.properties.permissions
        match_result = _find_matching_pattern_and_condition(
            operation_name, operation_lower, is_data_action, permissions
        )

        allowing_roles.append(
            RoleAllowingOperation(
                role_id=role_id,
                role_name=role.properties.role_name,
                role_type=role.properties.type or DEFAULT_ROLE_TYPE,
                matched_pattern=match_result.matched_pattern or "*",
                actions_count=len(control_effective),
                data_actions_count=len(data_effective),
                has_condition=match_result.has_condition,
                condition_text=match_result.condition_text,
            )
        )

    allowing_roles.sort(key=lambda x: x.role_name.lower())

    # Cache result
    cache.set(cache_key, allowing_roles)

    return allowing_roles


# =============================================================================
# Role Detail Helpers
# =============================================================================
async def get_role_from_cache_or_db(
    cache: CacheContainer,
    session_local: async_sessionmaker,
    role_snapshot_model: type[Role],
    role_history_model: type[RoleHistory],
    role_id: str,
    max_events: int = 200,
) -> RoleDetailResult:
    """Get role data from cache or fallback to database.

    Args:
        cache: The cache container instance.
        session_local: Async session factory.
        role_snapshot_model: Role SQLAlchemy model.
        role_history_model: RoleHistory SQLAlchemy model.
        role_id: The role ID to fetch.
        max_events: Maximum number of events to return.

    Returns:
        RoleDetailResult containing cached_role, definition, events, and first_scan.
    """
    import logging

    from sqlalchemy import func, select

    from azurerbac.telemetry import TimedDbQuery

    logger = logging.getLogger(__name__)

    cached_role = cache.get_role_by_id(role_id)
    cache_size = len(cache.cache.roles_by_id)
    logger.info(
        f"Role detail request: role_id={role_id}, "
        f"cache_size={cache_size}, cache_hit={cached_role is not None}"
    )

    if cached_role:
        first_scan = cache.cache.first_scan
        cached_events = cache.get_events_for_role(role_id)
        events_raw = cached_events[:max_events]
        return RoleDetailResult(
            cached_role=cached_role,
            definition=cached_role.definition,
            events=events_raw,
            first_scan=first_scan,
        )

    logger.warning("Cache miss for role %s, falling back to database", role_id)
    async with session_local() as session:
        role = await session.get(role_snapshot_model, role_id)
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

        first_scan = await session.scalar(select(func.min(RoleScanStatus.scan_timestamp)))
        if first_scan:
            first_scan = first_scan.replace(microsecond=0)

        # Get history entries for this role - RoleHistory has role_id directly
        async with TimedDbQuery("fetch_role_history") as timer:
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
                role_json=ev.role_definition.to_dict() if ev.role_definition else None,
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
    *,
    default_page: int = 1,
    default_limit: int = 25,
    default_days: int = 15,
) -> str:
    """Build redirect URL with canonical slug for role detail page.

    Args:
        request: FastAPI Request object.
        role_id: The role ID.
        expected_slug: The canonical URL slug.
        q: Search query parameter.
        page: Current page number.
        limit: Page size.
        days: Days filter.
        default_page: Default page number for omission check.
        default_limit: Default page size for omission check.
        default_days: Default days filter for omission check.

    Returns:
        Fully qualified redirect URL string.
    """
    from urllib.parse import urlencode

    # Build params dict, omitting defaults
    param_specs: list[tuple[str, object, object]] = [
        ("q", q, None),
        ("page", page if page != default_page else None, None),
        ("limit", limit if limit != default_limit else None, None),
        ("days", days if days != default_days else None, None),
    ]
    query_params = {name: str(val) for name, val, _ in param_specs if val is not None}

    if expected_slug:
        url = request.url_for("role_detail_slug", role_id=role_id, slug=expected_slug)
    else:
        url = request.url_for("role_detail", role_id=role_id)

    if query_params:
        url = f"{url}?{urlencode(query_params)}"
    return str(url)


def enrich_event_with_diff(ev: CachedChangeEvent) -> EnrichedChangeEvent:
    """Enrich a role change event with processed diff_json.

    Args:
        ev: CachedChangeEvent instance with diff_json field.

    Returns:
        EnrichedChangeEvent with processed diff and formatted JSON.
    """
    import json

    from azurerbac.azure.models import RoleDefinition
    from azurerbac.web.utils import role_json_pretty

    def to_clean_dict(role_json: dict | None) -> dict | None:
        """Parse role JSON through RoleDefinition model for consistent output."""
        return RoleDefinition.model_validate(role_json).to_dict() if role_json else None

    diff_json = ev.diff_json
    if diff_json:
        if before := diff_json.get("before_json"):
            diff_json = {**diff_json, "before_json": to_clean_dict(before)}
        if after := diff_json.get("after_json"):
            diff_json = {**diff_json, "after_json": to_clean_dict(after)}

    # Process role_json for created/initial_scan events
    role_json_pretty_str = ""
    if (role_json := ev.role_json) and (display_json := to_clean_dict(role_json)):
        role_json_pretty_str = role_json_pretty(display_json)

    return EnrichedChangeEvent(
        scan_timestamp=ev.scan_timestamp,
        azure_updated_on=ev.azure_updated_on,
        event_type=ev.event_type,
        summary=ev.summary,
        diff=diff_json,
        diff_pretty=(json.dumps(diff_json, indent=2, default=str) if diff_json else ""),
        role_json_pretty=role_json_pretty_str,
    )
