"""Dashboard service."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import DEFAULT_ROLE_TYPE
from azurerbac.core.enums import EventType, EventTypeFilter, SortOrder, StatusFilter
from azurerbac.core.utils import (
    ensure_utc,
    normalize_uuid_or_none,
    truncate_microseconds,
)
from azurerbac.matching.models import RoleNetPermissions
from azurerbac.telemetry import DbFallbackType, DbQueryName, TimedDbQuery
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

# Sentinel for roles with no net permissions in cache
_ZERO_PERMS: Final[RoleNetPermissions] = RoleNetPermissions(0, 0)

# Sort key functions for CachedRole (pre-enrichment sorting)
_CACHED_ROLE_SORT_KEYS: Final[dict[SortField, Callable[[CachedRole], Any]]] = {
    SortField.ID: lambda r: r.role_id.lower(),
    SortField.UPDATED: lambda r: r.updated_on or _MIN_DATETIME,
    SortField.NAME: lambda r: r.role_name.lower(),
}


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
        status=role.status.value,
        updated_on=role.updated_on,
        actions_count=actions_count,
        data_actions_count=data_actions_count,
    )


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

    async with TimedDbQuery(
        DbQueryName.FETCH_RECENT_CHANGES, fallback_type=DbFallbackType.RECENT_CHANGES
    ):
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
        async with TimedDbQuery(
            DbQueryName.FETCH_LAST_SCAN, fallback_type=DbFallbackType.SCAN_METADATA
        ):
            last_scan = await session.scalar(
                select(deps.RoleScanStatus.scan_timestamp)
                .order_by(deps.RoleScanStatus.scan_timestamp.desc())
                .limit(1)
            )
        last_scan = truncate_microseconds(last_scan)
        deps.app_cache.set_metadata(last_scan=last_scan)

    if first_scan is None:
        async with TimedDbQuery(
            DbQueryName.FETCH_FIRST_SCAN, fallback_type=DbFallbackType.SCAN_METADATA
        ):
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
    Sorts CachedRole objects directly and enriches only the paginated page
    (~25 items) instead of all ~800 roles.
    """
    cached_roles = deps.app_cache.cache.roles_by_id
    if not cached_roles:
        return None

    # Filter by status
    matching_roles = [
        role for role in cached_roles.values() if _matches_status(role.status, status_filter)
    ]

    # Sort CachedRole objects directly (avoids enriching all ~800 roles)
    sort_field = SortField.from_string(str(sort))
    reverse = order == SortOrder.DESC

    if sort_field == SortField.ACTIONS:
        cache = deps.app_cache
        matching_roles.sort(
            key=lambda r: (cache.get_role_net_permissions(r.role_id) or _ZERO_PERMS).control_count,
            reverse=reverse,
        )
    elif sort_field == SortField.DATA_ACTIONS:
        cache = deps.app_cache
        matching_roles.sort(
            key=lambda r: (cache.get_role_net_permissions(r.role_id) or _ZERO_PERMS).data_count,
            reverse=reverse,
        )
    else:
        key_func = _CACHED_ROLE_SORT_KEYS.get(sort_field, _CACHED_ROLE_SORT_KEYS[SortField.NAME])
        matching_roles.sort(key=key_func, reverse=reverse)

    # Paginate first, then enrich only the page (25 items vs 800+)
    params = PaginationParams(page=page, page_size=page_size)
    total_count = len(matching_roles)
    total_pages = PaginationInfo.count_pages(total_count, page_size)
    page_items = matching_roles[params.offset : params.offset + page_size]
    enriched_page = [enrich_role_with_counts(r, deps.app_cache) for r in page_items]

    return PaginatedResult(items=enriched_page, total_count=total_count, total_pages=total_pages)


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
    cached_count = deps.app_cache.get_role_page_count(count_cache_key)

    # Full cache hit - return immediately
    if cached_roles is not None and cached_count is not None:
        total_pages = PaginationInfo.count_pages(int(cached_count), page_size)
        return PaginatedResult(cached_roles, int(cached_count), total_pages)

    # Build from in-memory cache
    result = _fetch_roles_from_cache(deps, status_filter, sort, order, page, page_size)
    if result is None:
        raise RuntimeError("Role cache is empty - application not initialized")

    deps.app_cache.set_role_page(cache_key, result.items)
    deps.app_cache.set_role_page_count(count_cache_key, result.total_count)
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

    Results are ranked: exact match -> starts with -> contains.
    Within each tier, the user's chosen sort/order is applied.
    Sorts CachedRole objects directly and enriches only the paginated page.
    """
    query_lower = q.strip().lower()
    normalized_guid = normalize_uuid_or_none(q)

    matching_roles = [
        role
        for role in cached_roles.values()
        if _matches_status(role.status, status_filter)
        and _role_matches_search(role, query_lower, normalized_guid)
    ]

    # Two-pass stable sort on CachedRole directly (avoids enriching all matches).
    # Secondary sort first, then primary (relevance rank).
    sort_field = SortField.from_string(str(sort))
    reverse = order == SortOrder.DESC

    if sort_field == SortField.ACTIONS:
        _cache = _get_default_cache()
        matching_roles.sort(
            key=lambda r: (_cache.get_role_net_permissions(r.role_id) or _ZERO_PERMS).control_count,
            reverse=reverse,
        )
    elif sort_field == SortField.DATA_ACTIONS:
        _cache = _get_default_cache()
        matching_roles.sort(
            key=lambda r: (_cache.get_role_net_permissions(r.role_id) or _ZERO_PERMS).data_count,
            reverse=reverse,
        )
    else:
        secondary_key = _CACHED_ROLE_SORT_KEYS.get(
            sort_field, _CACHED_ROLE_SORT_KEYS[SortField.NAME]
        )
        matching_roles.sort(key=secondary_key, reverse=reverse)

    matching_roles.sort(
        key=lambda r: _role_search_rank(r.role_name, r.role_id, query_lower, normalized_guid),
    )

    # Paginate first, then enrich only the page
    params = PaginationParams(page=page, page_size=page_size)
    total_count = len(matching_roles)
    total_pages = PaginationInfo.count_pages(total_count, page_size)
    page_items = matching_roles[params.offset : params.offset + page_size]
    enriched_page = [enrich_role_with_counts(r) for r in page_items]

    return PaginatedResult(items=enriched_page, total_count=total_count, total_pages=total_pages)
