"""Dashboard service functions."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from enum import StrEnum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql import Select

from azurerbac.cache.models import CachedRole
from azurerbac.core.constants import RoleStatus
from azurerbac.core.utils import (
    ensure_utc,
    ensure_utc_or_min,
    normalize_uuid_or_none,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.cache import AppCache
    from azurerbac.core.models import Role, RoleHistory
    from azurerbac.web.dependencies import DashboardDeps


class SortField(StrEnum):
    """Valid sort fields for role listings."""

    ACTIONS = "actions"
    DATA_ACTIONS = "data_actions"
    ID = "id"
    UPDATED = "updated"
    NAME = "name"


# Sort key functions for enriched roles (maps SortField -> sort key function)
_ROLE_SORT_KEYS: Final[dict[str, Callable[[dict], Any]]] = {
    SortField.ACTIONS: lambda r: r.get("actions_count", 0),
    SortField.DATA_ACTIONS: lambda r: r.get("data_actions_count", 0),
    SortField.ID: lambda r: (r.get("role_id") or "").lower(),
    SortField.UPDATED: lambda r: r.get("updated_on") or "",
    SortField.NAME: lambda r: (r.get("role_name") or "").lower(),
}


def _calculate_total_pages(total_count: int, page_size: int) -> int:
    """Calculate total pages, ensuring at least 1 page."""
    return max(1, math.ceil(total_count / page_size))


def _build_status_filter(role_snapshot: type[Role], status_filter: str) -> Select[tuple[Role]]:
    """Build SQLAlchemy select statement with status filter.

    Args:
        role_snapshot: The Role model class.
        status_filter: "active", "deleted", or "all".

    Returns:
        SQLAlchemy select statement.
    """
    stmt = select(role_snapshot)
    if status_filter == RoleStatus.DELETED:
        return stmt.where(role_snapshot.status == RoleStatus.DELETED)
    if status_filter != "all":
        return stmt.where(role_snapshot.status == RoleStatus.ACTIVE)
    return stmt


def _get_sort_column(role_snapshot: type[Role], sort: str) -> Any:
    """Get the SQLAlchemy column for sorting.

    Args:
        role_snapshot: The Role model class.
        sort: Sort field name ("name", "id", "updated").

    Returns:
        SQLAlchemy column.
    """
    if sort == "id":
        return role_snapshot.role_id
    if sort == "updated":
        return role_snapshot.updated_on
    return role_snapshot.role_name


def _sort_enriched_roles(enriched_roles: list[dict], *, sort: str, order: str) -> None:
    """Sort enriched roles in-place by the specified field."""
    key_func = _ROLE_SORT_KEYS.get(sort, _ROLE_SORT_KEYS[SortField.NAME])
    enriched_roles.sort(key=key_func, reverse=(order == "desc"))


async def _execute_paginated_role_query(
    session: AsyncSession,
    deps: DashboardDeps,
    stmt: Select[tuple[Role]],
    *,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    needs_python_sort: bool,
) -> list[dict[str, Any]]:
    """Execute a role query with sorting and pagination.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        stmt: Base SQLAlchemy select statement
        sort: Sort field
        order: "asc" or "desc"
        page: Page number (1-indexed)
        page_size: Items per page
        needs_python_sort: If True, fetch all and sort in Python

    Returns:
        List of enriched role dicts for the requested page
    """
    offset = (page - 1) * page_size

    if needs_python_sort:
        stmt = stmt.order_by(deps.Role.role_name.asc())
        all_roles = (await session.execute(stmt)).scalars().all()
        enriched_roles = [enrich_role_with_counts(r) for r in all_roles]
        _sort_enriched_roles(enriched_roles, sort=sort, order=order)
        return enriched_roles[offset : offset + page_size]

    sort_column = _get_sort_column(deps.Role, sort)
    if order == "desc":
        stmt = stmt.order_by(sort_column.desc())
    else:
        stmt = stmt.order_by(sort_column.asc())
    stmt = stmt.offset(offset).limit(page_size)
    db_roles = (await session.execute(stmt)).scalars().all()
    return [enrich_role_with_counts(r) for r in db_roles]


def enrich_role_with_counts(role: Role | Any, app_cache: AppCache | None = None) -> dict[str, Any]:
    """Enrich a role object with actions_count and data_actions_count.

    Uses the pre-computed role net permissions cache from the recommender.
    Returns a dict with all role attributes plus counts.

    Args:
        role: A role object (SQLAlchemy model or cached dict-like object)
              with role_id, role_name, role_type, status, updated_on
        app_cache: Optional cache instance. If None, imports the singleton.

    Returns:
        dict with role data plus actions_count and data_actions_count
    """
    # Get counts from the recommender cache
    if app_cache is None:
        from azurerbac.cache import app_cache as _app_cache

        app_cache = _app_cache

    net_perms = app_cache.get_role_net_permissions(role.role_id)
    if net_perms:
        actions_count, data_actions_count = net_perms
    else:
        # Cache miss - counts not available (role_json stored in RoleHistory)
        actions_count = 0
        data_actions_count = 0

    return {
        "role_id": role.role_id,
        "role_name": role.role_name,
        "role_type": role.role_type,
        "status": role.status,
        "updated_on": role.updated_on,
        "actions_count": actions_count,
        "data_actions_count": data_actions_count,
    }


async def get_common_dashboard_data(deps: DashboardDeps) -> dict:
    """Get common data used by both recent and roles pages.

    Args:
        deps: Dashboard dependencies

    Returns:
        dict with total_roles, total_operations, last_scan, first_scan
    """
    # Check for disk cache updates
    deps.app_cache.reload_from_disk_if_needed()

    return {
        "total_roles": len(deps.app_cache.cache.roles_by_id),
        "total_operations": len(deps.app_cache.get_all_operations()),
        "last_scan": deps.app_cache.cache.last_scan,
        "first_scan": deps.app_cache.cache.first_scan,
    }


async def ensure_scan_metadata(
    session: AsyncSession,
    deps: DashboardDeps,
    last_scan: dt.datetime | None,
    first_scan: dt.datetime | None,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Ensure last_scan and first_scan are populated from DB if not cached.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        last_scan: Current last_scan value (may be None)
        first_scan: Current first_scan value (may be None)

    Returns:
        tuple of (last_scan, first_scan) with values populated
    """
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

    return last_scan, first_scan


def _event_matches_type(
    ev_type: str | None,
    event_type_filter: str,
    azure_updated: dt.datetime | None,
    scan_timestamp: dt.datetime | None,
    cutoff: dt.datetime,
) -> bool:
    """Check if an event matches the requested type filter and cutoff.

    Event types:
    - "created": Truly new roles (azure created == updated at detection time)
    - "initial_scan": Pre-existing roles discovered on first scan
    - "updated": Role was modified
    - "deleted": Role was deleted

    For created/updated/initial_scan, we use azure_updated_on for cutoff.
    For deleted events, we use scan_timestamp (when we detected the deletion).
    """
    if event_type_filter == "created":
        return ev_type == "created" and azure_updated is not None and azure_updated >= cutoff
    if event_type_filter == "updated":
        return ev_type == "updated" and azure_updated is not None and azure_updated >= cutoff
    if event_type_filter == "deleted":
        return ev_type == "deleted" and scan_timestamp is not None and scan_timestamp >= cutoff
    # "all" - match created, updated, deleted (but NOT initial_scan for recent changes)
    if ev_type == "created":
        return azure_updated is not None and azure_updated >= cutoff
    if ev_type == "updated":
        return azure_updated is not None and azure_updated >= cutoff
    if ev_type == "deleted":
        return scan_timestamp is not None and scan_timestamp >= cutoff
    return False


def filter_cached_events(
    cached_events: list[dict],
    deps: DashboardDeps,
    cutoff: dt.datetime,
    event_type: str,
) -> list:
    """Filter cached events by type and date.

    Args:
        cached_events: List of event dicts from cache
        deps: Dashboard dependencies (for role name lookup)
        cutoff: Datetime cutoff - events must be after this
        event_type: "created", "updated", "deleted", or "all"

    Returns:
        List of filtered event objects sorted by date descending
    """
    filtered_events = []
    for ev in cached_events:
        ev_type = ev.get("event_type")
        azure_updated = ensure_utc(ev.get("azure_updated_on"))
        scan_timestamp = ensure_utc(ev.get("scan_timestamp"))

        if _event_matches_type(ev_type, event_type, azure_updated, scan_timestamp, cutoff):
            role_id = ev.get("role_id")
            role_data = deps.app_cache.get_role_by_id(role_id) if role_id else None
            enriched_ev = {**ev}
            if role_data:
                enriched_ev["role_name"] = role_data.role_name
            filtered_events.append(SimpleNamespace(**enriched_ev))

    def get_sort_key(e: SimpleNamespace) -> dt.datetime:
        dt_val = getattr(e, "azure_updated_on", None) or getattr(e, "scan_timestamp", None)
        return ensure_utc_or_min(dt_val)

    filtered_events.sort(key=get_sort_key, reverse=True)
    return filtered_events


async def fetch_events_from_db(
    session: AsyncSession,
    deps: DashboardDeps,
    cutoff: dt.datetime,
    event_type: str,
) -> list[RoleHistory]:
    """Fetch events from database when cache is empty.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        cutoff: Datetime cutoff for filtering
        event_type: "created", "updated", "deleted", or "all"

    Returns:
        List of RoleHistory objects
    """
    # RoleHistory now contains both version and event data
    # For created/updated events we use azure_updated_on, for deleted we use scan_timestamp

    time_conditions = []

    if event_type == "created":
        # Truly new roles (event_type is already "created" vs "initial_scan")
        time_conditions.append(
            and_(
                deps.RoleHistory.event_type == "created",
                deps.RoleHistory.azure_updated_on >= cutoff,
            )
        )
    elif event_type == "updated":
        time_conditions.append(
            and_(
                deps.RoleHistory.event_type == "updated",
                deps.RoleHistory.azure_updated_on >= cutoff,
            )
        )
    elif event_type == "deleted":
        # For deleted events, filter by scan timestamp via join
        time_conditions.append(
            and_(
                deps.RoleHistory.event_type == "deleted",
                deps.RoleScanStatus.scan_timestamp >= cutoff,
            )
        )
    else:
        # "all" - created, updated, deleted (but NOT initial_scan)
        time_conditions = [
            and_(
                deps.RoleHistory.event_type == "created",
                deps.RoleHistory.azure_updated_on >= cutoff,
            ),
            and_(
                deps.RoleHistory.event_type == "updated",
                deps.RoleHistory.azure_updated_on >= cutoff,
            ),
            and_(
                deps.RoleHistory.event_type == "deleted",
                deps.RoleScanStatus.scan_timestamp >= cutoff,
            ),
        ]

    # Join with RoleScanStatus for ordering and deleted event filtering
    return list(
        (
            await session.execute(
                select(deps.RoleHistory)
                .join(
                    deps.RoleScanStatus,
                    deps.RoleHistory.scan_id == deps.RoleScanStatus.id,
                    isouter=True,
                )
                .where(or_(*time_conditions))
                .order_by(deps.RoleScanStatus.scan_timestamp.desc())
            )
        )
        .scalars()
        .all()
    )


async def fetch_roles_paginated(
    session: AsyncSession,
    deps: DashboardDeps,
    status_filter: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    needs_python_sort: bool,
) -> tuple[list[dict[str, Any]], int, int]:
    """Fetch paginated roles, using cache when possible.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        status_filter: "active", "deleted", or "all"
        sort: Sort field ("name", "id", "updated", "actions", "data_actions")
        order: "asc" or "desc"
        page: Page number (1-indexed)
        page_size: Number of items per page
        needs_python_sort: Whether sorting requires Python (for actions/data_actions)

    Returns:
        tuple of (roles list, total count, total pages)
    """
    cache_key = f"roles:{status_filter}::{sort}:{order}:{page}:{page_size}"
    count_cache_key = f"roles_count:{status_filter}:"

    cached_roles = deps.app_cache.get_role_page(cache_key)
    cached_count = deps.app_cache.get_role_page(count_cache_key)

    if cached_roles is not None and cached_count is not None:
        total_pages = _calculate_total_pages(int(cached_count), page_size)
        return cached_roles, int(cached_count), total_pages

    # Cache miss - fetch from database
    stmt = _build_status_filter(deps.Role, status_filter)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_filtered_roles = await session.scalar(count_stmt) or 0
    deps.app_cache.set_role_page(count_cache_key, total_filtered_roles)

    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    roles = await _execute_paginated_role_query(
        session,
        deps,
        stmt,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        needs_python_sort=needs_python_sort,
    )

    deps.app_cache.set_role_page(cache_key, roles)
    return roles, total_filtered_roles, total_pages


async def search_roles(
    session: AsyncSession,
    deps: DashboardDeps,
    q: str,
    status_filter: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    needs_python_sort: bool,
    exact_match: str | None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Search roles using cache first, fallback to DB.

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        q: Search query string
        status_filter: "active", "deleted", or "all"
        sort: Sort field
        order: "asc" or "desc"
        page: Page number (1-indexed)
        page_size: Number of items per page
        needs_python_sort: Whether sorting requires Python
        exact_match: If set, perform exact match search

    Returns:
        tuple of (roles list, total count, total pages)
    """
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
    status_filter: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    exact_match: str | None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Search roles in memory cache.

    Args:
        cached_roles: Dict of role_id -> CachedRole
        q: Search query string
        status_filter: "active", "deleted", or "all"
        sort: Sort field
        order: "asc" or "desc"
        page: Page number (1-indexed)
        page_size: Number of items per page
        exact_match: If set, perform exact match search

    Returns:
        tuple of (roles list, total count, total pages)
    """
    q_lower = q.strip().lower()

    normalized_guid = normalize_uuid_or_none(q)

    matching_roles: list[CachedRole] = []
    for role in cached_roles.values():
        role_name_lower = role.role_name.lower()
        role_id_lower = role.role_id.lower()

        # Apply status filter
        if status_filter == "deleted" and role.status != "deleted":
            continue
        if status_filter == "active" and role.status != "active":
            continue

        # Check for GUID match
        guid_match = normalized_guid is not None and role.role_id == normalized_guid

        # Apply search filter
        if exact_match:
            if q_lower in {role_name_lower, role_id_lower} or guid_match:
                matching_roles.append(role)
        elif q_lower in role_name_lower or q_lower in role_id_lower or guid_match:
            matching_roles.append(role)

    total_filtered_roles = len(matching_roles)
    total_pages = _calculate_total_pages(total_filtered_roles, page_size)

    # Enrich with counts - CachedRole has all fields needed by enrich_role_with_counts
    enriched_roles = [enrich_role_with_counts(r) for r in matching_roles]

    # Sort
    _sort_enriched_roles(enriched_roles, sort=sort, order=order)

    # Paginate
    offset = (page - 1) * page_size
    roles = enriched_roles[offset : offset + page_size]

    return roles, total_filtered_roles, total_pages


async def search_roles_in_db(
    session: AsyncSession,
    deps: DashboardDeps,
    q: str,
    status_filter: str,
    sort: str,
    order: str,
    page: int,
    page_size: int,
    needs_python_sort: bool,
    exact_match: str | None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Search roles in database (fallback when cache is empty).

    Args:
        session: SQLAlchemy async session
        deps: Dashboard dependencies
        q: Search query string
        status_filter: "active", "deleted", or "all"
        sort: Sort field
        order: "asc" or "desc"
        page: Page number (1-indexed)
        page_size: Number of items per page
        needs_python_sort: Whether sorting requires Python
        exact_match: If set, perform exact match search

    Returns:
        tuple of (roles list, total count, total pages)
    """
    stmt = _build_status_filter(deps.Role, status_filter)

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

    roles = await _execute_paginated_role_query(
        session,
        deps,
        stmt,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        needs_python_sort=needs_python_sort,
    )

    return roles, total_filtered_roles, total_pages
