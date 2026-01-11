"""Dashboard service functions."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Protocol

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE, EventType, RoleStatus
from azurerbac.core.utils import (
    ensure_utc,
    ensure_utc_or_min,
    normalize_uuid_or_none,
)
from azurerbac.web.services.models import (
    DashboardSummary,
    PaginatedResult,
    RoleWithCounts,
    ScanMetadata,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.core.models import Role, RoleHistory
    from azurerbac.web.dependencies import DashboardDeps


# =============================================================================
# Enums and Constants
# =============================================================================


class SortField(StrEnum):
    """Valid sort fields for role listings."""

    ACTIONS = "actions"
    DATA_ACTIONS = "data_actions"
    ID = "id"
    UPDATED = "updated"
    NAME = "name"


class SortOrder(StrEnum):
    """Sort order direction."""

    ASC = "asc"
    DESC = "desc"


class StatusFilter(StrEnum):
    """Valid status filters for role listings."""

    ACTIVE = "active"
    DELETED = "deleted"
    ALL = "all"


class EventTypeFilter(StrEnum):
    """Valid event type filters for recent changes."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ALL = "all"


_MIN_DATETIME: Final[dt.datetime] = dt.datetime.min.replace(tzinfo=dt.UTC)

_ROLE_SORT_KEYS: Final[dict[str, Callable[[RoleWithCounts], Any]]] = {
    SortField.ACTIONS: lambda r: r.actions_count,
    SortField.DATA_ACTIONS: lambda r: r.data_actions_count,
    SortField.ID: lambda r: r.role_id.lower(),
    SortField.UPDATED: lambda r: r.updated_on or _MIN_DATETIME,
    SortField.NAME: lambda r: r.role_name.lower(),
}


# =============================================================================
# Protocols for dependency injection (testability)
# =============================================================================


class PermissionsCacheProtocol(Protocol):
    """Protocol for cache objects that provide role net permissions."""

    def get_role_net_permissions(self, role_id: str) -> tuple[int, int] | None: ...


# =============================================================================
# Pagination dataclass (reduces parameter counts)
# =============================================================================


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Pagination parameters."""

    page: int
    page_size: int
    sort: str | SortField = SortField.NAME
    order: str | SortOrder = SortOrder.ASC

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def is_descending(self) -> bool:
        return self.order == SortOrder.DESC


# =============================================================================
# Role enrichment
# =============================================================================


def _get_default_cache() -> PermissionsCacheProtocol:
    """Get the default cache singleton."""
    from azurerbac.cache import get_cache_service

    return get_cache_service().container


def enrich_role_with_counts(
    role: Role | Any,
    cache: PermissionsCacheProtocol | None = None,
) -> RoleWithCounts:
    """Enrich a role object with actions_count and data_actions_count.

    Uses the pre-computed role net permissions cache from the recommender.

    Args:
        role: A role object with role_id, role_name, role_type, status, updated_on
        cache: Optional cache instance for testing. If None, uses singleton.

    Returns:
        RoleWithCounts with role data and action counts
    """
    if cache is None:
        cache = _get_default_cache()

    net_perms = cache.get_role_net_permissions(role.role_id)
    actions_count, data_actions_count = net_perms if net_perms else (0, 0)

    # Handle status - could be RoleStatus enum or string
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


# =============================================================================
# Sorting helpers
# =============================================================================


def _sort_enriched_roles(
    enriched_roles: list[RoleWithCounts],
    *,
    sort: str | SortField,
    order: str | SortOrder,
) -> None:
    """Sort enriched roles in-place by the specified field."""
    try:
        sort_field = SortField(sort)
    except ValueError:
        sort_field = SortField.NAME
    key_func = _ROLE_SORT_KEYS.get(sort_field, _ROLE_SORT_KEYS[SortField.NAME])
    enriched_roles.sort(key=key_func, reverse=(order == SortOrder.DESC))


def _get_sort_column(role_model: type[Role], sort: str | SortField) -> Any:
    """Get the SQLAlchemy column for sorting."""
    columns: dict[str | SortField, Any] = {
        SortField.ID: role_model.role_id,
        SortField.UPDATED: role_model.updated_on,
    }
    return columns.get(sort, role_model.role_name)


# =============================================================================
# Status filtering
# =============================================================================


def _matches_status(role_status: str, status_filter: str | StatusFilter) -> bool:
    """Check if a role status matches the filter."""
    if status_filter == StatusFilter.ALL:
        return True
    return role_status == status_filter


def _build_status_filter(
    role_model: type[Role], status_filter: str | StatusFilter
) -> Select[tuple[Role]]:
    """Build SQLAlchemy select statement with status filter."""
    stmt = select(role_model)
    if status_filter == StatusFilter.DELETED:
        return stmt.where(role_model.status == RoleStatus.DELETED)
    if status_filter != StatusFilter.ALL:
        return stmt.where(role_model.status == RoleStatus.ACTIVE)
    return stmt


# =============================================================================
# Pagination helpers
# =============================================================================


def _calculate_total_pages(total_count: int, page_size: int) -> int:
    """Calculate total pages, ensuring at least 1 page."""
    return max(1, math.ceil(total_count / page_size))


def _paginate_list[T](items: list[T], params: PaginationParams) -> PaginatedResult[T]:
    """Apply pagination to a list."""
    total_count = len(items)
    total_pages = _calculate_total_pages(total_count, params.page_size)
    page_items = items[params.offset : params.offset + params.page_size]
    return PaginatedResult(items=page_items, total_count=total_count, total_pages=total_pages)


# =============================================================================
# Event filtering
# =============================================================================


def _get_event_timestamp(
    ev_type: str | EventType | None,
    azure_updated: dt.datetime | None,
    scan_ts: dt.datetime | None,
) -> dt.datetime | None:
    """Get the relevant timestamp for an event type."""
    match ev_type:
        case EventType.DELETED:
            return scan_ts
        case _:
            return azure_updated


def _event_matches_filter(
    ev_type: str | EventType | None,
    event_type_filter: str | EventTypeFilter,
    azure_updated: dt.datetime | None,
    scan_timestamp: dt.datetime | None,
    cutoff: dt.datetime,
) -> bool:
    """Check if an event matches the requested type filter and cutoff."""
    # initial_scan events are never shown in recent changes
    if ev_type == EventType.INITIAL_SCAN:
        return False

    # Determine which timestamp to use for this event type
    relevant_timestamp = _get_event_timestamp(ev_type, azure_updated, scan_timestamp)
    if relevant_timestamp is None or relevant_timestamp < cutoff:
        return False

    # Match specific filter or "all"
    return event_type_filter in {EventTypeFilter.ALL, ev_type}


def _get_event_sort_key(e: CachedChangeEvent) -> dt.datetime:
    """Get sort key for events (newest first)."""
    dt_val = e.azure_updated_on or e.scan_timestamp
    return ensure_utc_or_min(dt_val)


def filter_cached_events(
    cached_events: list[CachedChangeEvent],
    deps: DashboardDeps,
    cutoff: dt.datetime,
    event_type: str | EventTypeFilter,
) -> list[CachedChangeEvent]:
    """Filter cached events by type and date.

    Args:
        cached_events: List of CachedChangeEvent from cache
        deps: Dashboard dependencies (for role name lookup)
        cutoff: Datetime cutoff - events must be after this
        event_type: EventTypeFilter enum or string: "created", "updated", "deleted", or "all"

    Returns:
        List of filtered CachedChangeEvent sorted by date descending
    """
    filtered_events: list[CachedChangeEvent] = []

    for ev in cached_events:
        azure_updated = ensure_utc(ev.azure_updated_on)
        scan_timestamp = ensure_utc(ev.scan_timestamp)

        matches = _event_matches_filter(
            ev.event_type, event_type, azure_updated, scan_timestamp, cutoff
        )
        if not matches:
            continue

        # Update role_name from cache if available (role may have been renamed)
        role_data = deps.app_cache.get_role_by_id(ev.role_id) if ev.role_id else None
        if role_data and role_data.role_name != ev.role_name:
            enriched_ev = replace(ev, role_name=role_data.role_name)
            filtered_events.append(enriched_ev)
        else:
            filtered_events.append(ev)

    filtered_events.sort(key=_get_event_sort_key, reverse=True)
    return filtered_events


# =============================================================================
# Database queries
# =============================================================================


def _build_event_condition(
    history_model: type[RoleHistory],
    scan_model: type,
    ev_type: str | EventType,
    cutoff: dt.datetime,
) -> ColumnElement[bool]:
    """Build a single event type condition for database query."""
    match ev_type:
        case EventType.DELETED:
            return and_(
                history_model.event_type == ev_type,
                scan_model.scan_timestamp >= cutoff,
            )
        case _:
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
    """Fetch events from database when cache is empty.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        cutoff: Datetime cutoff for filtering
        event_type: EventTypeFilter value (created, updated, deleted, or all)

    Returns:
        List of RoleHistory objects
    """
    # Determine which event types to include
    match event_type:
        case EventTypeFilter.ALL:
            event_types: list[str | EventType] = [
                EventType.CREATED,
                EventType.UPDATED,
                EventType.DELETED,
            ]
        case _:
            event_types = [event_type]

    # Build conditions for each event type
    conditions = [
        _build_event_condition(deps.RoleHistory, deps.RoleScanStatus, et, cutoff)
        for et in event_types
    ]

    # Join with RoleScanStatus for ordering and deleted event filtering
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
    """Execute a role query with sorting and pagination.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        stmt: Base SQLAlchemy select statement
        params: Pagination parameters
        needs_python_sort: If True, fetch all and sort in Python

    Returns:
        List of RoleWithCounts for the requested page
    """
    if needs_python_sort:
        stmt = stmt.order_by(deps.Role.role_name.asc())
        all_roles = (await session.execute(stmt)).scalars().all()
        enriched_roles = [enrich_role_with_counts(r) for r in all_roles]
        _sort_enriched_roles(enriched_roles, sort=params.sort, order=params.order)
        return enriched_roles[params.offset : params.offset + params.page_size]

    sort_column = _get_sort_column(deps.Role, params.sort)
    if params.order == SortOrder.DESC:
        stmt = stmt.order_by(sort_column.desc())
    else:
        stmt = stmt.order_by(sort_column.asc())

    stmt = stmt.offset(params.offset).limit(params.page_size)
    db_roles = (await session.execute(stmt)).scalars().all()
    return [enrich_role_with_counts(r) for r in db_roles]


# =============================================================================
# Dashboard data
# =============================================================================


async def get_common_dashboard_data(deps: DashboardDeps) -> DashboardSummary:
    """Get common data used by both recent and roles pages."""
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
    """Ensure last_scan and first_scan are populated from DB if not cached."""
    if last_scan is None:
        last_scan = await session.scalar(
            select(deps.RoleScanStatus.scan_timestamp)
            .order_by(deps.RoleScanStatus.scan_timestamp.desc())
            .limit(1)
        )
        if last_scan:
            last_scan = last_scan.replace(microsecond=0)
        deps.app_cache.set_metadata(last_scan=last_scan)

    if first_scan is None:
        first_scan = await session.scalar(select(func.min(deps.RoleScanStatus.scan_timestamp)))
        if first_scan:
            first_scan = first_scan.replace(microsecond=0)
        deps.app_cache.set_metadata(first_scan=first_scan)

    return ScanMetadata(last_scan=last_scan, first_scan=first_scan)


# =============================================================================
# Role fetching (paginated)
# =============================================================================


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
    """Fetch paginated roles, using cache when possible."""
    cache_key = f"roles:{status_filter}::{sort}:{order}:{page}:{page_size}"
    count_cache_key = f"roles_count:{status_filter}:"

    cached_roles = deps.app_cache.get_role_page(cache_key)
    cached_count = deps.app_cache.get_role_page(count_cache_key)

    if cached_roles is not None and cached_count is not None:
        total_pages = _calculate_total_pages(int(cached_count), page_size)
        return PaginatedResult(cached_roles, int(cached_count), total_pages)

    # Cache miss - fetch from database
    stmt = _build_status_filter(deps.Role, status_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_filtered_roles = await session.scalar(count_stmt) or 0
    deps.app_cache.set_role_page(count_cache_key, total_filtered_roles)

    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    params = PaginationParams(page=page, page_size=page_size, sort=sort, order=order)
    roles = await _execute_paginated_role_query(session, deps, stmt, params, needs_python_sort)

    deps.app_cache.set_role_page(cache_key, roles)
    return PaginatedResult(roles, total_filtered_roles, total_pages)


# =============================================================================
# Role search
# =============================================================================


def _role_matches_search(
    role: CachedRole,
    query_lower: str,
    normalized_guid: str | None,
    exact_match: bool,
) -> bool:
    """Check if a role matches the search query."""
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

    # Filter roles matching search and status
    matching_roles = [
        role
        for role in cached_roles.values()
        if _matches_status(role.status, status_filter)
        and _role_matches_search(role, query_lower, normalized_guid, is_exact)
    ]

    # Enrich with counts
    enriched_roles = [enrich_role_with_counts(r) for r in matching_roles]

    # Sort
    _sort_enriched_roles(enriched_roles, sort=sort, order=order)

    # Paginate
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

    conditions: list[ColumnElement[bool]]
    if exact_match:
        conditions = [
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
    total_filtered_roles = await session.scalar(count_stmt) or 0
    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    params = PaginationParams(page=page, page_size=page_size, sort=sort, order=order)
    roles = await _execute_paginated_role_query(session, deps, stmt, params, needs_python_sort)

    return PaginatedResult(roles, total_filtered_roles, total_pages)
