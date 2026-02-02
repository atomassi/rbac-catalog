"""Pages service functions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE
from azurerbac.core.enums import SortOrder
from azurerbac.core.utils import truncate_microseconds
from azurerbac.web.services.models import (
    EnrichedChangeEvent,
    OperationSearchParams,
    OperationSortField,
    RoleAllowingOperation,
    RoleDetailResult,
    RoleEffectivePermissions,
    RolePermissionAnalyzer,
)

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from azurerbac.cache import CacheService
    from azurerbac.core.models import Role, RoleHistory

logger = logging.getLogger(__name__)


def operation_matches_search(op: OperationData, query_lower: str) -> bool:
    """Check if operation matches search query."""
    return op.matches_search(query_lower)


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
        if (q_lower is None or operation_matches_search(op, q_lower))
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

        # TODO: cache match_result in the index during build time
        # to avoid analyzer call per role at query time
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
    from azurerbac.core.diffing import RoleDiff
    from azurerbac.web.utils import role_json_pretty

    def parse_role(role_json: dict | None) -> RoleDefinition | None:
        """Parse role JSON to RoleDefinition model."""
        return RoleDefinition.model_validate(role_json) if role_json else None

    diff: RoleDiff | None = RoleDiff.from_dict(ev.diff_json)

    # Only process before_json/after_json if diff exists and at least one side is present
    # (delete events only have "changes", not the full JSON)
    if diff is not None and (diff.before_json is not None or diff.after_json is not None):
        before_role = parse_role(diff.before_json)
        after_role = parse_role(diff.after_json)

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
    if (role_json := ev.role_json) and (parsed := parse_role(role_json)):
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
