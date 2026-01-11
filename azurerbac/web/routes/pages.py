"""Page route handlers for the Azure RBAC Catalog."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from azurerbac.core.enums import SortOrder
from azurerbac.web.constants import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    DEFAULT_PAGE,
    MAX_PAGE_NUMBER,
    MAX_PAGE_SIZE,
    MAX_ROLE_EVENTS,
)
from azurerbac.web.dependencies import PagesDeps, get_pages_deps
from azurerbac.web.routes.models import OperationWithCount
from azurerbac.web.services.models import PaginationInfo
from azurerbac.web.services.pages import (
    DataActionFilter,
    OperationSearchParams,
    OperationSortField,
    build_role_redirect_url,
    compute_role_effective_permissions,
    enrich_event_with_diff,
    filter_operations,
    get_role_from_cache_or_db,
    get_roles_allowing_operation,
    sort_operations,
)
from azurerbac.web.utils import clamp, role_json_pretty, slugify

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


@router.get("/roles/{role_id}", response_class=HTMLResponse, name="role_detail")
@router.get("/roles/{role_id}/{slug}", response_class=HTMLResponse, name="role_detail_slug")
async def role_detail(
    request: Request,
    role_id: str,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    slug: str | None = None,
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    days: int = DEFAULT_DAYS,
) -> Response:
    """Role detail page showing role definition and change history."""
    # Normalize role_id to canonical UUID format (handles both with/without dashes)
    try:
        role_id = str(uuid.UUID(role_id))
    except ValueError:
        return deps.templates.TemplateResponse(request, "404.html", status_code=404)

    # Get role from cache or database
    result = await get_role_from_cache_or_db(
        deps.app_cache,
        deps.SessionLocal,
        deps.Role,
        deps.RoleHistory,
        role_id,
        max_events=MAX_ROLE_EVENTS,
    )

    if result.cached_role is None:
        return deps.templates.TemplateResponse(request, "404.html", status_code=404)

    role = result.cached_role
    role_def = result.definition

    # SEO: Validate slug and redirect if necessary
    role_name = getattr(role, "role_name", "") or (role_def.role_name if role_def else "")
    expected_slug = slugify(role_name)

    if slug != expected_slug:
        url = build_role_redirect_url(
            request,
            role_id,
            expected_slug,
            q,
            page,
            limit,
            days,
            default_page=DEFAULT_PAGE,
            default_limit=DEFAULT_LIMIT,
            default_days=DEFAULT_DAYS,
        )
        return RedirectResponse(url=url, status_code=301)

    # Enrich events with processed diff_json
    enriched = [enrich_event_with_diff(ev) for ev in result.events]

    # Use RoleDefinition for clean output (excludes isServiceRole)
    display_json = role_def.to_dict() if role_def else {}
    all_ops = deps.app_cache.get_all_operations()
    effective_perms = (
        compute_role_effective_permissions(role_def, all_ops, deps.app_cache) if role_def else None
    )

    return deps.templates.TemplateResponse(
        request,
        "role.html",
        {
            "role": role,
            "events": enriched,
            "first_scan": result.first_scan,
            "role_json_pretty": role_json_pretty(display_json),
            "effective_perms": effective_perms,
            "q": q,
            "page": page,
            "limit": limit,
            "days": days,
        },
    )


@router.get("/operations", response_class=HTMLResponse)
async def operations_list(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    is_data_action: str | None = None,
    provider: str | None = None,
    sort: str = OperationSortField.NAME,
    order: str = SortOrder.ASC,
) -> Response:
    """Operations list page - shows all Azure RBAC operations."""
    logger.info("Operations list: q='%s' page=%d provider=%s", q or "", page, provider or "all")

    # Enforce bounds on pagination parameters
    page_size = clamp(limit, 1, MAX_PAGE_SIZE)
    page = clamp(page, 1, MAX_PAGE_NUMBER)

    # Get all operations from cache
    all_operations = deps.app_cache.get_all_operations()
    total_operations = len(all_operations)

    # Get unique providers for filter dropdown (precomputed during cache build)
    providers = deps.app_cache.cache.unique_providers

    # Parse is_data_action filter
    is_data_action_filter = DataActionFilter.parse(is_data_action)

    # Build search params and filter operations (operates on OperationData)
    search_params = OperationSearchParams(
        query=q,
        is_data_action=is_data_action_filter,
        provider=provider,
        sort=sort,
        order=order,
    )
    filtered_ops = filter_operations(all_operations, search_params)

    # Sort operations - returns list of (OperationData, role_count) tuples
    sorted_ops_with_counts = sort_operations(filtered_ops, sort, order, deps.app_cache)

    total_filtered = len(sorted_ops_with_counts)
    pagination = PaginationInfo.compute(total_filtered, page, page_size)
    page_slice = sorted_ops_with_counts[pagination.start_idx : pagination.end_idx]

    # Convert only the paginated slice to typed models
    page_operations = [
        OperationWithCount.from_operation(op, role_count) for op, role_count in page_slice
    ]

    return deps.templates.TemplateResponse(
        request,
        "operations.html",
        {
            "request": request,
            "operations": page_operations,
            "total_operations": total_operations,
            "total_filtered": total_filtered,
            "page": page,
            "limit": page_size,
            "total_pages": pagination.total_pages,
            "q": q,
            "is_data_action": is_data_action_filter,
            "provider": provider,
            "providers": providers,
            "sort": sort,
            "order": order,
            "last_scan": deps.app_cache.cache.last_scan,
        },
    )


@router.get(
    "/operations/{operation_name:path}",
    response_class=HTMLResponse,
    name="operation_detail",
)
async def operation_detail(
    operation_name: str,
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    q: str | None = None,
    page: int = DEFAULT_PAGE,
    limit: int = DEFAULT_LIMIT,
    is_data_action: str | None = None,
    provider: str | None = None,
) -> Response:
    """Operation detail page - shows operation info and roles that allow it."""
    # URL decode the operation name (handles both encoded and unencoded paths)
    decoded_name = unquote(operation_name)

    # Parse is_data_action filter for template
    is_data_action_filter = DataActionFilter.parse(is_data_action)

    # Find the operation by name using the indexed lookup
    operation = deps.app_cache.cache.ops_by_name_lower.get(decoded_name.lower())

    if not operation:
        # Return 404-like response
        return deps.templates.TemplateResponse(
            request,
            "operation_detail.html",
            {
                "request": request,
                "operation": {
                    "name": decoded_name,
                    "description": None,
                    "display_name": None,
                    "is_data_action": False,
                },
                "allowing_roles": [],
                "q": q,
                "page": page,
                "limit": limit,
                "is_data_action": is_data_action_filter,
                "provider": provider,
            },
            status_code=404,
        )

    # Find roles that allow this operation
    allowing_roles = get_roles_allowing_operation(
        operation.name, operation.is_data_action, deps.app_cache
    )

    return deps.templates.TemplateResponse(
        request,
        "operation_detail.html",
        {
            "request": request,
            "operation": operation,
            "allowing_roles": allowing_roles,
            "q": q,
            "page": page,
            "limit": limit,
            "is_data_action": is_data_action_filter,
            "provider": provider,
        },
    )


@router.get("/recommend", response_class=HTMLResponse)
async def recommend_page(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """Role Recommender page."""
    logger.info("Recommend page loaded")
    # Get count from cache - if 0, cache hasn't been initialized yet
    ops_count = len(deps.app_cache.get_all_operations())

    return deps.templates.TemplateResponse(
        request,
        "recommend.html",
        {
            "operations_loaded": ops_count > 0,
            "operations_count": ops_count,
        },
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """About/FAQ page."""
    logger.info("About page loaded")
    # Preserve ai=1 parameter if set
    ai_mode = request.query_params.get("ai") == "1"

    # Get counts from cache - if 0, cache hasn't been initialized yet
    roles_count = len(deps.app_cache.cache.roles_by_id)
    ops_count = len(deps.app_cache.get_all_operations())

    return deps.templates.TemplateResponse(
        request,
        "about.html",
        {
            "total_roles": roles_count,
            "total_operations": ops_count,
            "ai_mode": ai_mode,
        },
    )
