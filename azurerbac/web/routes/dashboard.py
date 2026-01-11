"""Dashboard route handlers for the Azure RBAC Catalog."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from azurerbac.web.constants import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    DEFAULT_PAGE,
    MAX_DAYS,
    MAX_PAGE_NUMBER,
    MAX_PAGE_SIZE,
)
from azurerbac.web.dependencies import DashboardDeps, get_dashboard_deps
from azurerbac.web.services.dashboard import (
    SortField,
    SortOrder,
    StatusFilter,
    ensure_scan_metadata,
    fetch_events_from_db,
    fetch_roles_paginated,
    filter_cached_events,
    get_common_dashboard_data,
    search_roles,
)
from azurerbac.web.services.models import PaginationInfo
from azurerbac.web.utils import clamp

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])


# ─────────────────────────────────────────────────────────────────────────────
# Route Handlers (Public API)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/recent", response_class=HTMLResponse)
async def recent_changes(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
    q: str | None = None,
    days: int = DEFAULT_DAYS,
    event_type: str = "all",
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    ai: int | None = None,
) -> Response:
    """Recent changes page - shows recent role changes."""
    logger.info(
        "Dashboard /recent: days=%d event_type=%s page=%d limit=%d", days, event_type, page, limit
    )
    return await _render_recent(
        request=request,
        deps=deps,
        q=q,
        days=days,
        event_type=event_type,
        page=page,
        limit=limit,
        ai=ai,
    )


@router.get("/roles", response_class=HTMLResponse)
async def roles_list(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    sort: str = SortField.NAME,
    order: str = SortOrder.ASC,
    status_filter: str = StatusFilter.ACTIVE,
    ai: int | None = None,
    exact_match: str | None = None,
) -> Response:
    """Roles list page - shows all Azure built-in roles."""
    logger.info(
        "Dashboard /roles: q='%s' page=%d sort=%s status=%s", q or "", page, sort, status_filter
    )
    return await _render_roles(
        request=request,
        deps=deps,
        q=q,
        page=page,
        limit=limit,
        sort=sort,
        order=order,
        status_filter=status_filter,
        ai=ai,
        exact_match=exact_match,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Private Helpers (Implementation Details)
# ─────────────────────────────────────────────────────────────────────────────
async def _render_recent(
    request: Request,
    deps: DashboardDeps,
    q: str | None = None,
    days: int = DEFAULT_DAYS,
    event_type: str = "all",
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    ai: int | None = None,
) -> Response:
    """Render the recent changes page."""
    # If search query present, redirect to /roles
    if q:
        from urllib.parse import urlencode

        params: dict[str, str | int] = {"q": q}
        if ai:
            params["ai"] = ai
        return RedirectResponse(url=f"/roles?{urlencode(params)}", status_code=302)

    # Get common data
    common = await get_common_dashboard_data(deps)
    days = clamp(days, 1, MAX_DAYS)
    page = clamp(page, 1, MAX_PAGE_NUMBER)
    limit = clamp(limit, 1, MAX_PAGE_SIZE)

    events = []
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    async with deps.SessionLocal() as session:
        # Try to use cached events
        if cached_events := deps.app_cache.get_change_events():
            events = filter_cached_events(cached_events, deps, cutoff, event_type)
        else:
            # Fallback to database query
            events = await fetch_events_from_db(session, deps, cutoff, event_type)

        scan_meta = await ensure_scan_metadata(session, deps, common.last_scan, common.first_scan)
        last_scan, first_scan = scan_meta.last_scan, scan_meta.first_scan

    # Pagination: calculate total and slice
    total_events = len(events)
    pagination = PaginationInfo.compute(total_events, page, limit)
    paginated_events = events[pagination.start_idx : pagination.end_idx]

    return deps.templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": paginated_events,
            "total_events": total_events,
            "roles": [],
            "total_roles": common.total_roles or 0,
            "total_operations": common.total_operations or 0,
            "last_scan": last_scan,
            "first_scan": first_scan,
            "q": q,
            "tab": "recent",
            "page": page,
            "limit": limit,
            "total_pages": pagination.total_pages,
            "days": days,
            "sort": "name",
            "order": "asc",
            "status_filter": "active",
            "event_type": event_type,
            "ai": ai,
            "exact_match": None,
        },
    )


async def _render_roles(
    request: Request,
    deps: DashboardDeps,
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    sort: str = "name",
    order: str = "asc",
    status_filter: str = "active",
    ai: int | None = None,
    exact_match: str | None = None,
) -> Response:
    """Render the roles list page."""
    # Get common data
    common = await get_common_dashboard_data(deps)

    # Enforce bounds
    page_size = clamp(limit, 1, MAX_PAGE_SIZE)
    page = clamp(page, 1, MAX_PAGE_NUMBER)

    roles = []
    total_pages = 1
    # "updated" also needs Python sort: Role.updated_on is a property that reads from RoleHistory
    needs_python_sort = sort in ("actions", "data_actions", "updated")

    async with deps.SessionLocal() as session:
        if not q:
            # No search - use pagination cache
            result = await fetch_roles_paginated(
                session, deps, status_filter, sort, order, page, page_size, needs_python_sort
            )
        else:
            # Search query - use cache first
            result = await search_roles(
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

        roles = result.items
        total_pages = result.total_pages

        scan_meta = await ensure_scan_metadata(session, deps, common.last_scan, common.first_scan)
        last_scan, first_scan = scan_meta.last_scan, scan_meta.first_scan

    return deps.templates.TemplateResponse(
        request,
        "index.html",
        {
            "events": [],
            "roles": roles,
            "total_roles": common.total_roles or 0,
            "total_operations": common.total_operations or 0,
            "last_scan": last_scan,
            "first_scan": first_scan,
            "q": q,
            "tab": "roles",
            "page": page,
            "limit": page_size,
            "total_pages": total_pages,
            "days": DEFAULT_DAYS,
            "sort": sort,
            "order": order,
            "status_filter": status_filter,
            "event_type": "all",
            "ai": ai,
            "exact_match": exact_match,
        },
    )
