"""Dashboard route handlers."""

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from azurerbac.core.enums import SortOrder, StatusFilter
from azurerbac.web.constants import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    DEFAULT_PAGE,
    MAX_DAYS,
    MAX_PAGE_NUMBER,
    MAX_PAGE_SIZE,
    MAX_QUERY_LENGTH,
)
from azurerbac.web.dependencies import DashboardDeps, get_dashboard_deps
from azurerbac.web.services.dashboard import (
    ensure_scan_metadata,
    fetch_events_from_db,
    fetch_roles_paginated,
    filter_cached_events,
    get_common_dashboard_data,
    search_roles_in_cache,
)
from azurerbac.web.services.models import PaginationInfo, ScanMetadata, SortField

if TYPE_CHECKING:
    from azurerbac.web.services.models import RoleWithCounts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


@dataclass(slots=True)
class DashboardContext:
    """Template context for dashboard pages."""

    # Tab and pagination
    tab: str
    page: int
    limit: int
    total_pages: int
    # Filters
    q: str | None = None
    days: int = DEFAULT_DAYS
    sort: str = SortField.NAME
    order: str = SortOrder.ASC
    status_filter: str = StatusFilter.ACTIVE
    event_type: str = "all"
    # Data
    events: list[Any] = field(default_factory=list)
    total_events: int = 0
    roles: "list[RoleWithCounts]" = field(default_factory=list)
    total_roles: int = 0
    total_operations: int = 0
    last_scan: dt.datetime | None = None
    first_scan: dt.datetime | None = None


@router.api_route("/recent", methods=["GET", "HEAD"], response_class=HTMLResponse)
@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def recent_changes(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
    event_type: str = "all",
    page: Annotated[int, Query(ge=1, le=MAX_PAGE_NUMBER)] = DEFAULT_PAGE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_LIMIT,
) -> Response:
    """Recent changes page. Also serves as the home page (/) to avoid redirect latency."""
    logger.info(
        "Dashboard /recent: days=%d event_type=%s page=%d limit=%d", days, event_type, page, limit
    )
    # If search query present, redirect to /roles
    if q:
        params: dict[str, str | int] = {"q": q}
        return RedirectResponse(url=f"/roles?{urlencode(params)}", status_code=302)

    common = get_common_dashboard_data(deps)

    events: list[Any] = []
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    # Build cache key for filtered events (days + event_type)
    events_cache_key = f"{days}:{event_type}"

    # Resolve events from cache layers; only open a DB session on full miss
    needs_db = False
    if cached_filtered := deps.app_cache.get_filtered_events(events_cache_key):
        events = cached_filtered
    elif cached_events := deps.app_cache.get_change_events():
        events = filter_cached_events(cached_events, deps, cutoff, event_type)
        deps.app_cache.set_filtered_events(events_cache_key, events)
    else:
        needs_db = True

    # Scan metadata may also need DB if not yet cached
    scan_needs_db = common.last_scan is None or common.first_scan is None

    if needs_db or scan_needs_db:
        async with deps.SessionLocal() as session:
            if needs_db:
                events = await fetch_events_from_db(session, deps, cutoff, event_type)
            scan_meta = await ensure_scan_metadata(
                session, deps, common.last_scan, common.first_scan
            )
    else:
        scan_meta = ScanMetadata(last_scan=common.last_scan, first_scan=common.first_scan)

    total_events = len(events)
    pagination = PaginationInfo.compute(total_events, page, limit)

    ctx = DashboardContext(
        tab="recent",
        page=page,
        limit=limit,
        total_pages=pagination.total_pages,
        q=q,
        days=days,
        event_type=event_type,
        events=events[pagination.start_idx : pagination.end_idx],
        total_events=total_events,
        total_roles=common.total_roles or 0,
        total_operations=common.total_operations or 0,
        last_scan=scan_meta.last_scan,
        first_scan=scan_meta.first_scan,
    )
    return deps.templates.TemplateResponse(request, "index.html", asdict(ctx))


@router.api_route("/roles", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def roles_list(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE_NUMBER)] = DEFAULT_PAGE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_LIMIT,
    sort: str = SortField.NAME,
    order: str = SortOrder.ASC,
    status_filter: str = StatusFilter.ACTIVE,
) -> Response:
    """Roles list page."""
    logger.info(
        "Dashboard /roles: q='%s' page=%d sort=%s status=%s", q or "", page, sort, status_filter
    )
    common = get_common_dashboard_data(deps)

    if not q:
        result = await fetch_roles_paginated(deps, status_filter, sort, order, page, limit)
    else:
        cached_roles = deps.app_cache.cache.roles_by_id
        if not cached_roles:
            raise RuntimeError("Role cache is empty - application not initialized")
        result = search_roles_in_cache(
            cached_roles,
            q,
            status_filter,
            sort,
            order,
            page,
            limit,
        )

    # Only open a DB session if scan metadata is not yet cached
    if common.last_scan is None or common.first_scan is None:
        async with deps.SessionLocal() as session:
            scan_meta = await ensure_scan_metadata(
                session, deps, common.last_scan, common.first_scan
            )
    else:
        scan_meta = ScanMetadata(last_scan=common.last_scan, first_scan=common.first_scan)

    ctx = DashboardContext(
        tab="roles",
        page=page,
        limit=limit,
        total_pages=result.total_pages,
        q=q,
        sort=sort,
        order=order,
        status_filter=status_filter,
        roles=result.items,
        total_roles=common.total_roles or 0,
        total_operations=common.total_operations or 0,
        last_scan=scan_meta.last_scan,
        first_scan=scan_meta.first_scan,
    )
    return deps.templates.TemplateResponse(request, "index.html", asdict(ctx))
