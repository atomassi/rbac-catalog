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

    from azurerbac.cache import CacheContainer
    from azurerbac.core.models import Role, RoleHistory

logger = logging.getLogger(__name__)


def operation_matches_search(op: OperationData, query_lower: str) -> bool:
    """Check if operation matches search query."""
    return op.matches_search(query_lower)


def filter_operations(
    operations: list[OperationData],
    params: OperationSearchParams,
) -> list[OperationData]:
    """Filter operations based on search parameters."""
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
    cache: CacheContainer | None = None,
) -> list[tuple[OperationData, int]]:
    """Sort operations and return with role counts."""
    from azurerbac.cache import get_cache_service

    cache_resolved = cache if cache is not None else get_cache_service().container

    # Build tuples with role count for sorting (needed for "roles" sort and enrichment)
    ops_with_count = [(op, cache_resolved.get_operation_role_count(op.name)) for op in operations]

    if sort == OperationSortField.ROLES:
        # Sort by role count
        ops_with_count.sort(key=lambda x: x[1], reverse=(order == SortOrder.DESC))
    else:
        key_func = _OPERATION_SORT_KEYS.get(sort, _OPERATION_SORT_KEYS[OperationSortField.NAME])
        ops_with_count.sort(key=lambda x: key_func(x[0]), reverse=(order == SortOrder.DESC))

    return ops_with_count


def compute_role_effective_permissions(
    role: RoleDefinition,
    all_operations: list[OperationData],
    cache: CacheContainer | None = None,
) -> RoleEffectivePermissions:
    """Compute effective permissions for a role."""
    analyzer = RolePermissionAnalyzer(role, cache=cache)
    return analyzer.get_effective_permissions(all_operations)


def _operation_in_set(operation_lower: str, operation_set: set[str]) -> bool:
    """Check if operation is in set."""
    return operation_lower in operation_set


def _get_cache(cache: CacheContainer | None) -> CacheContainer:
    """Get cache container."""
    if cache is not None:
        return cache
    from azurerbac.cache import get_cache_service

    return get_cache_service().container


def get_roles_allowing_operation(
    operation_name: str,
    is_data_action: bool,
    cache: CacheContainer | None = None,
) -> list[RoleAllowingOperation]:
    """Find roles allowing a specific operation."""
    cache_resolved = _get_cache(cache)

    # Check cache first
    cache_key = f"roles_allowing_op:{operation_name.lower()}:{is_data_action}"
    if (cached := cache_resolved.get_allowing_roles(cache_key)) is not None:
        return cached

    if not (all_roles := cache_resolved.get_all_roles()):
        return []

    logger.debug(
        "allowing_roles cache miss for %s, scanning %d roles", operation_name, len(all_roles)
    )

    allowing_roles: list[RoleAllowingOperation] = []
    operation_lowered = operation_name.lower()
    all_operations = cache_resolved.get_all_operations()

    for role in all_roles:
        analyzer = RolePermissionAnalyzer(role, cache=cache_resolved)

        # Use cached coverage if available, otherwise compute on-demand
        # This handles the case when the coverage cache is invalidated due to staleness
        cached_coverage = analyzer.get_cached_coverage()
        if cached_coverage:
            control_effective, data_effective = cached_coverage
        else:
            control_effective, data_effective = analyzer.compute_coverage(all_operations)

        operation_set = data_effective if is_data_action else control_effective

        if not _operation_in_set(operation_lowered, operation_set):
            continue

        match_result = analyzer.find_matching_pattern(operation_name, is_data_action=is_data_action)

        allowing_roles.append(
            RoleAllowingOperation.from_match(
                role_id=analyzer.role_id,
                role_name=role.properties.role_name,
                role_type=role.properties.type or DEFAULT_ROLE_TYPE,
                control_count=len(control_effective),
                data_count=len(data_effective),
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
    cache: CacheContainer | None = None,
) -> RoleDetailResult:
    """Get role data from cache or database."""
    from sqlalchemy import func, select

    from azurerbac.cache import get_cache_service
    from azurerbac.telemetry import TimedDbQuery

    cache_resolved = cache if cache is not None else get_cache_service().container

    cached_role = cache_resolved.get_role_by_id(role_id)
    cache_size = len(cache_resolved.cache.roles_by_id)
    logger.info(
        f"Role detail request: role_id={role_id}, "
        f"cache_size={cache_size}, cache_hit={cached_role is not None}"
    )

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
        first_scan = truncate_microseconds(first_scan)

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
