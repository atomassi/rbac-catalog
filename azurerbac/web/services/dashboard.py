"""Dashboard service."""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE, EventType, RoleStatus
from azurerbac.core.enums import EventTypeFilter, SortOrder, StatusFilter
from azurerbac.core.utils import (
    ensure_utc,
    ensure_utc_or_min,
    normalize_uuid_or_none,
    truncate_microseconds,
)
from azurerbac.matching.models import RoleNetPermissions
from azurerbac.telemetry import TimedDbQuery
from azurerbac.web.services.models import (
    DashboardSummary,
    PaginatedResult,
    PaginationParams,
    RoleWithCounts,
    ScanMetadata,
    SortField,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.core.models import Role, RoleHistory
    from azurerbac.web.dependencies import DashboardDeps


_MIN_DATETIME: Final[dt.datetime] = dt.datetime.min.replace(tzinfo=dt.UTC)

_ROLE_SORT_KEYS: Final[dict[SortField, Callable[[RoleWithCounts], Any]]] = {
    SortField.ACTIONS: lambda r: r.actions_count,
    SortField.DATA_ACTIONS: lambda r: r.data_actions_count,
    SortField.ID: lambda r: r.role_id.lower(),
    SortField.UPDATED: lambda r: r.updated_on or _MIN_DATETIME,
    SortField.NAME: lambda r: r.role_name.lower(),
}


class PermissionsCacheProtocol(Protocol):
    """Protocol for cache providing role net permissions."""

    def get_role_net_permissions(self, role_id: str) -> RoleNetPermissions | None: ...


def _calculate_total_pages(total_count: int, page_size: int) -> int:
    return max(1, math.ceil(total_count / page_size))


def _paginate_list[T](items: list[T], params: PaginationParams) -> PaginatedResult[T]:
    """Apply pagination to a list."""
    total_count = len(items)
    total_pages = _calculate_total_pages(total_count, params.page_size)
    page_items = items[params.offset : params.offset + params.page_size]
    return PaginatedResult(items=page_items, total_count=total_count, total_pages=total_pages)


def _get_default_cache() -> PermissionsCacheProtocol:
    """Get the default cache singleton."""
    from azurerbac.cache import get_cache_service

    return get_cache_service()


def enrich_role_with_counts(
    role: Role | Any,
    cache: PermissionsCacheProtocol | None = None,
) -> RoleWithCounts:
    """Enrich role with action counts from cache."""
    if cache is None:
        cache = _get_default_cache()

    net_perms = cache.get_role_net_permissions(role.role_id)
    actions_count = net_perms.control_count if net_perms else 0
    data_actions_count = net_perms.data_count if net_perms else 0

    # Normalize status (handles both enum and string)
    status_value = role.status.value if hasattr(role.status, "value") else str(role.status)

    return RoleWithCounts(
        role_id=role.role_id,
        role_name=role.role_name,
        role_type=role.role_type or DEFAULT_ROLE_TYPE,
        status=status_value,
        updated_on=role.updated_on,
        actions_count=actions_count,
        data_actions_count=data_actions_count,
    )


def _sort_enriched_roles(
    enriched_roles: list[RoleWithCounts],
    *,
    sort: str | SortField,
    order: str | SortOrder,
) -> None:
    """Sort enriched roles in-place."""
    sort_field = SortField.from_string(str(sort))
    key_func = _ROLE_SORT_KEYS.get(sort_field, _ROLE_SORT_KEYS[SortField.NAME])
    enriched_roles.sort(key=key_func, reverse=(order == SortOrder.DESC))


def _get_sort_column(role_model: type[Role], sort: str | SortField) -> Any:
    """Get SQLAlchemy column for sorting."""
    columns: dict[str | SortField, Any] = {
        SortField.ID: role_model.role_id,
        SortField.UPDATED: role_model.updated_on,
    }
    return columns.get(sort, role_model.role_name)


def _matches_status(role_status: str, status_filter: str | StatusFilter) -> bool:
    """Check if role status matches filter."""
    if status_filter == StatusFilter.ALL:
        return True
    return role_status == status_filter


def _build_status_filter(
    role_model: type[Role], status_filter: str | StatusFilter
) -> Select[tuple[Role]]:
    """Build SQLAlchemy select with status filter."""
    stmt = select(role_model)
    if status_filter == StatusFilter.DELETED:
        return stmt.where(role_model.status == RoleStatus.DELETED)
    if status_filter != StatusFilter.ALL:
        return stmt.where(role_model.status == RoleStatus.ACTIVE)
    return stmt


def _get_event_timestamp(
    ev_type: str | EventType | None,
    azure_updated: dt.datetime | None,
    scan_ts: dt.datetime | None,
) -> dt.datetime | None:
    """Get relevant timestamp for event type."""
    return scan_ts if ev_type == EventType.DELETED else azure_updated


def _event_matches_filter(
    ev_type: str | EventType | None,
    event_type_filter: str | EventTypeFilter,
    azure_updated: dt.datetime | None,
    scan_timestamp: dt.datetime | None,
    cutoff: dt.datetime,
) -> bool:
    """Check if event matches filter and cutoff."""
    if ev_type == EventType.INITIAL_SCAN:
        return False

    relevant_timestamp = _get_event_timestamp(ev_type, azure_updated, scan_timestamp)
    if relevant_timestamp is None or relevant_timestamp < cutoff:
        return False

    return event_type_filter in {EventTypeFilter.ALL, ev_type}


def _get_event_sort_key(e: CachedChangeEvent) -> dt.datetime:
    """Sort key for events."""
    return ensure_utc_or_min(e.azure_updated_on or e.scan_timestamp)


def filter_cached_events(
    cached_events: list[CachedChangeEvent],
    deps: DashboardDeps,
    cutoff: dt.datetime,
    event_type: str | EventTypeFilter,
) -> list[CachedChangeEvent]:
    """Filter and sort cached events."""
    filtered: list[CachedChangeEvent] = []

    for ev in cached_events:
        azure_updated = ensure_utc(ev.azure_updated_on)
        scan_timestamp = ensure_utc(ev.scan_timestamp)

        if not _event_matches_filter(
            ev.event_type, event_type, azure_updated, scan_timestamp, cutoff
        ):
            continue

        # Update role_name if role was renamed
        role_data = deps.app_cache.get_role_by_id(ev.role_id) if ev.role_id else None
        if role_data and role_data.role_name != ev.role_name:
            filtered.append(replace(ev, role_name=role_data.role_name))
        else:
            filtered.append(ev)

    filtered.sort(key=_get_event_sort_key, reverse=True)
    return filtered


def _build_event_condition(
    history_model: type[RoleHistory],
    scan_model: type,
    ev_type: str | EventType,
    cutoff: dt.datetime,
) -> ColumnElement[bool]:
    """Build event type condition for query."""
    if ev_type == EventType.DELETED:
        return and_(
            history_model.event_type == ev_type,
            scan_model.scan_timestamp >= cutoff,
        )
    return and_(
        history_model.event_type == ev_type,
        history_model.azure_updated_on >= cutoff,
    )


async def fetch_events_from_db(
    session: AsyncSession,
    deps: DashboardDeps,
    cutoff: dt.datetime,
    event_type: str | EventTypeFilter,
) -> list[RoleHistory]:
    """Fetch events from database."""
    if event_type == EventTypeFilter.ALL:
        event_types: list[str | EventType] = [
            EventType.CREATED,
            EventType.UPDATED,
            EventType.DELETED,
        ]
    else:
        event_types = [event_type]

    conditions = [
        _build_event_condition(deps.RoleHistory, deps.RoleScanStatus, et, cutoff)
        for et in event_types
    ]

    async with TimedDbQuery("fetch_recent_changes", fallback_type="recent_changes"):
        result = await session.execute(
            select(deps.RoleHistory)
            .join(
                deps.RoleScanStatus,
                deps.RoleHistory.scan_id == deps.RoleScanStatus.id,
                isouter=True,
            )
            .where(or_(*conditions))
            .order_by(deps.RoleScanStatus.scan_timestamp.desc())
        )
        return list(result.scalars().all())


async def _execute_paginated_role_query(
    session: AsyncSession,
    deps: DashboardDeps,
    stmt: Select[tuple[Role]],
    params: PaginationParams,
    needs_python_sort: bool,
) -> list[RoleWithCounts]:
    """Execute role query with sorting and pagination.

    For actions/data_actions sort: requires Python sort (values from cache).
    For updated sort: uses SQL subquery (efficient, no N+1).
    For name/id sort: uses simple DB column sort.
    """
    sort_field = params.sort_field

    # actions/data_actions need Python sort (values come from cache)
    if sort_field in (SortField.ACTIONS, SortField.DATA_ACTIONS):
        stmt = stmt.order_by(deps.Role.role_name.asc())
        async with TimedDbQuery("fetch_roles_paginated_all", fallback_type="dashboard_roles"):
            all_roles = (await session.execute(stmt)).scalars().all()
        enriched_roles = [enrich_role_with_counts(r) for r in all_roles]
        _sort_enriched_roles(enriched_roles, sort=params.sort, order=params.order)
        return enriched_roles[params.offset : params.offset + params.page_size]

    # updated sort: use subquery to get max(azure_updated_on) per role
    if sort_field == SortField.UPDATED:
        # Subquery: get latest azure_updated_on per role_id
        latest_update = (
            select(
                deps.RoleHistory.role_id,
                func.max(deps.RoleHistory.azure_updated_on).label("latest_updated"),
            )
            .group_by(deps.RoleHistory.role_id)
            .subquery()
        )

        # Join roles with the subquery and sort by latest_updated
        stmt = stmt.outerjoin(latest_update, deps.Role.role_id == latest_update.c.role_id)
        sort_col = latest_update.c.latest_updated
        if params.order == SortOrder.DESC:
            stmt = stmt.order_by(sort_col.desc().nulls_last())
        else:
            stmt = stmt.order_by(sort_col.asc().nulls_last())

        stmt = stmt.offset(params.offset).limit(params.page_size)
        async with TimedDbQuery("fetch_roles_paginated_updated", fallback_type="dashboard_roles"):
            db_roles = (await session.execute(stmt)).scalars().all()
        return [enrich_role_with_counts(r) for r in db_roles]

    # name/id sort: simple column sort
    sort_column = _get_sort_column(deps.Role, params.sort)
    if params.order == SortOrder.DESC:
        stmt = stmt.order_by(sort_column.desc())
    else:
        stmt = stmt.order_by(sort_column.asc())

    stmt = stmt.offset(params.offset).limit(params.page_size)
    async with TimedDbQuery("fetch_roles_paginated", fallback_type="dashboard_roles"):
        db_roles = (await session.execute(stmt)).scalars().all()
    return [enrich_role_with_counts(r) for r in db_roles]


async def get_common_dashboard_data(deps: DashboardDeps) -> DashboardSummary:
    """Get summary data for dashboard pages."""
    return DashboardSummary(
        total_roles=len(deps.app_cache.cache.roles_by_id),
        total_operations=len(deps.app_cache.get_all_operations()),
        last_scan=deps.app_cache.cache.last_scan,
        first_scan=deps.app_cache.cache.first_scan,
    )


async def ensure_scan_metadata(
    session: AsyncSession,
    deps: DashboardDeps,
    last_scan: dt.datetime | None,
    first_scan: dt.datetime | None,
) -> ScanMetadata:
    """Ensure scan timestamps are populated from DB if not cached."""
    if last_scan is None:
        async with TimedDbQuery("fetch_last_scan", fallback_type="scan_metadata"):
            last_scan = await session.scalar(
                select(deps.RoleScanStatus.scan_timestamp)
                .order_by(deps.RoleScanStatus.scan_timestamp.desc())
                .limit(1)
            )
        last_scan = truncate_microseconds(last_scan)
        deps.app_cache.set_metadata(last_scan=last_scan)

    if first_scan is None:
        async with TimedDbQuery("fetch_first_scan", fallback_type="scan_metadata"):
            first_scan = await session.scalar(select(func.min(deps.RoleScanStatus.scan_timestamp)))
        first_scan = truncate_microseconds(first_scan)
        deps.app_cache.set_metadata(first_scan=first_scan)

    return ScanMetadata(last_scan=last_scan, first_scan=first_scan)


async def fetch_roles_paginated(
    session: AsyncSession,
    deps: DashboardDeps,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
    needs_python_sort: bool,
) -> PaginatedResult[RoleWithCounts]:
    """Fetch paginated roles with caching."""
    cache_key = f"roles:{status_filter}::{sort}:{order}:{page}:{page_size}"
    count_cache_key = f"roles_count:{status_filter}"

    cached_roles = deps.app_cache.get_role_page(cache_key)
    cached_count = deps.app_cache.get_role_page(count_cache_key)

    # Full cache hit - return immediately
    if cached_roles is not None and cached_count is not None:
        total_pages = _calculate_total_pages(int(cached_count), page_size)
        return PaginatedResult(cached_roles, int(cached_count), total_pages)

    stmt = _build_status_filter(deps.Role, status_filter)

    # Use cached count if available, otherwise fetch from DB
    if cached_count is not None:
        total_filtered_roles = int(cached_count)
    else:
        count_stmt = select(func.count()).select_from(stmt.subquery())
        async with TimedDbQuery("fetch_roles_count", fallback_type="dashboard_roles"):
            total_filtered_roles = await session.scalar(count_stmt) or 0
        deps.app_cache.set_role_page(count_cache_key, total_filtered_roles)

    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    params = PaginationParams(page=page, page_size=page_size, sort=sort, order=order)
    roles = await _execute_paginated_role_query(session, deps, stmt, params, needs_python_sort)

    deps.app_cache.set_role_page(cache_key, roles)
    return PaginatedResult(roles, total_filtered_roles, total_pages)


def _role_matches_search(
    role: CachedRole,
    query_lower: str,
    normalized_guid: str | None,
    exact_match: bool,
) -> bool:
    """Check if role matches search query."""
    role_name_lower = role.role_name.lower()
    role_id_lower = role.role_id.lower()
    guid_match = normalized_guid is not None and role.role_id == normalized_guid

    if exact_match:
        return query_lower in {role_name_lower, role_id_lower} or guid_match
    return query_lower in role_name_lower or query_lower in role_id_lower or guid_match


async def search_roles(
    session: AsyncSession,
    deps: DashboardDeps,
    q: str,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
    needs_python_sort: bool,
    exact_match: str | None,
) -> PaginatedResult[RoleWithCounts]:
    """Search roles using cache first, fallback to DB."""
    cached_roles = deps.app_cache.cache.roles_by_id

    if cached_roles:
        return search_roles_in_cache(
            cached_roles, q, status_filter, sort, order, page, page_size, exact_match
        )

    # Cache empty - fallback tracked via TimedDbQuery
    return await search_roles_in_db(
        session,
        deps,
        q,
        status_filter,
        sort,
        order,
        page,
        page_size,
        needs_python_sort,
        exact_match,
    )


def search_roles_in_cache(
    cached_roles: dict[str, CachedRole],
    q: str,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
    exact_match: str | None,
) -> PaginatedResult[RoleWithCounts]:
    """Search roles in memory cache."""
    query_lower = q.strip().lower()
    normalized_guid = normalize_uuid_or_none(q)
    is_exact = exact_match is not None

    matching_roles = [
        role
        for role in cached_roles.values()
        if _matches_status(role.status, status_filter)
        and _role_matches_search(role, query_lower, normalized_guid, is_exact)
    ]

    enriched_roles = [enrich_role_with_counts(r) for r in matching_roles]
    _sort_enriched_roles(enriched_roles, sort=sort, order=order)

    params = PaginationParams(page=page, page_size=page_size)
    return _paginate_list(enriched_roles, params)


async def search_roles_in_db(
    session: AsyncSession,
    deps: DashboardDeps,
    q: str,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
    needs_python_sort: bool,
    exact_match: str | None,
) -> PaginatedResult[RoleWithCounts]:
    """Search roles in database (fallback when cache is empty)."""
    stmt = _build_status_filter(deps.Role, status_filter)

    if exact_match:
        conditions: list[ColumnElement[bool]] = [
            func.lower(deps.Role.role_name) == func.lower(q.strip()),
            func.lower(deps.Role.role_id) == func.lower(q.strip()),
        ]
    else:
        search_term = f"%{q}%"
        conditions = [
            deps.Role.role_name.ilike(search_term),
            deps.Role.role_id.ilike(search_term),
        ]

    normalized_id = normalize_uuid_or_none(q)
    if normalized_id is not None:
        conditions.append(deps.Role.role_id == normalized_id)

    stmt = stmt.where(or_(*conditions))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    async with TimedDbQuery("search_roles_count", fallback_type="search_roles"):
        total_filtered_roles = await session.scalar(count_stmt) or 0
    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    params = PaginationParams(page=page, page_size=page_size, sort=sort, order=order)
    roles = await _execute_paginated_role_query(session, deps, stmt, params, needs_python_sort)

    return PaginatedResult(roles, total_filtered_roles, total_pages)
