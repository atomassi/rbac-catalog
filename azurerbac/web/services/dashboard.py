"""Dashboard service."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE, EventType
from azurerbac.core.enums import EventTypeFilter, SortOrder, StatusFilter
from azurerbac.core.utils import (
    ensure_utc,
    normalize_uuid_or_none,
    truncate_microseconds,
)
from azurerbac.telemetry import TimedDbQuery
from azurerbac.web.services.models import (
    DashboardSummary,
    PaginatedResult,
    PaginationInfo,
    PaginationParams,
    RoleWithCounts,
    ScanMetadata,
    SortField,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.cache.service import CacheService
    from azurerbac.core.models import RoleHistory
    from azurerbac.web.dependencies import DashboardDeps


_MIN_DATETIME: Final[dt.datetime] = dt.datetime.min.replace(tzinfo=dt.UTC)

_ROLE_SORT_KEYS: Final[dict[SortField, Callable[[RoleWithCounts], Any]]] = {
    SortField.ACTIONS: lambda r: r.actions_count,
    SortField.DATA_ACTIONS: lambda r: r.data_actions_count,
    SortField.ID: lambda r: r.role_id.lower(),
    SortField.UPDATED: lambda r: r.updated_on or _MIN_DATETIME,
    SortField.NAME: lambda r: r.role_name.lower(),
}


def _paginate_list[T](items: list[T], params: PaginationParams) -> PaginatedResult[T]:
    """Apply pagination to a list."""
    total_count = len(items)
    total_pages = PaginationInfo.count_pages(total_count, params.page_size)
    page_items = items[params.offset : params.offset + params.page_size]
    return PaginatedResult(items=page_items, total_count=total_count, total_pages=total_pages)


def _get_default_cache() -> CacheService:
    """Get the default cache singleton."""
    from azurerbac.cache import get_cache_service

    return get_cache_service()


def enrich_role_with_counts(
    role: CachedRole,
    cache: CacheService | None = None,
) -> RoleWithCounts:
    """Enrich role with action counts from cache."""
    if cache is None:
        cache = _get_default_cache()

    net_perms = cache.get_role_net_permissions(role.role_id)
    actions_count = net_perms.control_count if net_perms else 0
    data_actions_count = net_perms.data_count if net_perms else 0

    return RoleWithCounts(
        role_id=role.role_id,
        role_name=role.role_name,
        role_type=role.role_type or DEFAULT_ROLE_TYPE,
        status=role.status.value if isinstance(role.status, Enum) else str(role.status),
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


def _matches_status(role_status: str, status_filter: str | StatusFilter) -> bool:
    """Check if role status matches filter."""
    if status_filter == StatusFilter.ALL:
        return True
    return role_status == status_filter


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
    return e.effective_timestamp


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


def get_common_dashboard_data(deps: DashboardDeps) -> DashboardSummary:
    """Get summary data for dashboard pages."""
    return DashboardSummary(
        total_roles=deps.app_cache.cache.active_roles_count,
        total_operations=deps.app_cache.cache.metadata.operations_count,
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


def _fetch_roles_from_cache(
    deps: DashboardDeps,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
) -> PaginatedResult[RoleWithCounts] | None:
    """Fetch paginated roles from cache.

    Returns None if cache is empty, otherwise returns paginated result.
    This avoids DB queries since all role data is already cached.
    """
    cached_roles = deps.app_cache.cache.roles_by_id
    if not cached_roles:
        return None

    # Filter by status
    matching_roles = [
        role for role in cached_roles.values() if _matches_status(role.status, status_filter)
    ]

    # Enrich, sort, and paginate
    enriched_roles = [enrich_role_with_counts(r) for r in matching_roles]
    _sort_enriched_roles(enriched_roles, sort=sort, order=order)

    params = PaginationParams(page=page, page_size=page_size)
    return _paginate_list(enriched_roles, params)


async def fetch_roles_paginated(
    deps: DashboardDeps,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
) -> PaginatedResult[RoleWithCounts]:
    """Fetch paginated roles from cache."""
    cache_key = f"roles:{status_filter}::{sort}:{order}:{page}:{page_size}"
    count_cache_key = f"roles_count:{status_filter}"

    cached_roles = deps.app_cache.get_role_page(cache_key)
    cached_count = deps.app_cache.get_role_page(count_cache_key)

    # Full cache hit - return immediately
    if cached_roles is not None and cached_count is not None:
        total_pages = PaginationInfo.count_pages(int(cached_count), page_size)
        return PaginatedResult(cached_roles, int(cached_count), total_pages)

    # Build from in-memory cache
    result = _fetch_roles_from_cache(deps, status_filter, sort, order, page, page_size)
    if result is None:
        raise RuntimeError("Role cache is empty - application not initialized")

    deps.app_cache.set_role_page(cache_key, result.items)
    deps.app_cache.set_role_page(count_cache_key, result.total_count)
    return result


def _role_matches_search(
    role: CachedRole,
    query_lower: str,
    normalized_guid: str | None,
) -> bool:
    """Check if role matches search query (contains match)."""
    role_name_lower = role.role_name.lower()
    role_id_lower = role.role_id.lower()
    guid_match = normalized_guid is not None and role.role_id == normalized_guid
    return query_lower in role_name_lower or query_lower in role_id_lower or guid_match


def _role_search_rank(
    role_name: str,
    role_id: str,
    query_lower: str,
    normalized_guid: str | None,
) -> int:
    """Return a relevance rank for sorting search results (lower = better match).

    Ranking tiers (same logic as the compare page):
      0 = exact name/ID match
      1 = name starts with query
      2 = name/ID contains query
    """
    role_name_lower = role_name.lower()
    role_id_lower = role_id.lower()
    guid_match = normalized_guid is not None and role_id == normalized_guid

    if query_lower in {role_name_lower, role_id_lower} or guid_match:
        return 0
    if role_name_lower.startswith(query_lower):
        return 1
    return 2


def search_roles_in_cache(
    cached_roles: dict[str, CachedRole],
    q: str,
    status_filter: str | StatusFilter,
    sort: str | SortField,
    order: str | SortOrder,
    page: int,
    page_size: int,
) -> PaginatedResult[RoleWithCounts]:
    """Search roles in memory cache with relevance ranking.

    Results are ranked: exact match → starts with → contains.
    Within each tier, the user's chosen sort/order is applied.
    """
    query_lower = q.strip().lower()
    normalized_guid = normalize_uuid_or_none(q)

    matching_roles = [
        role
        for role in cached_roles.values()
        if _matches_status(role.status, status_filter)
        and _role_matches_search(role, query_lower, normalized_guid)
    ]

    enriched_roles = [enrich_role_with_counts(r) for r in matching_roles]

    # Two-pass stable sort: secondary sort first, then primary (relevance rank).
    # Python's stable sort preserves secondary order within each rank tier.
    sort_field = SortField.from_string(str(sort))
    secondary_key = _ROLE_SORT_KEYS.get(sort_field, _ROLE_SORT_KEYS[SortField.NAME])
    enriched_roles.sort(key=secondary_key, reverse=(order == SortOrder.DESC))
    enriched_roles.sort(
        key=lambda r: _role_search_rank(r.role_name, r.role_id, query_lower, normalized_guid),
    )

    params = PaginationParams(page=page, page_size=page_size)
    return _paginate_list(enriched_roles, params)
