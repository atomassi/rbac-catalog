"""Page route handlers."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from azurerbac.core.enums import SortOrder
from azurerbac.core.utils import slugify
from azurerbac.web.constants import (
    DEFAULT_DAYS,
    DEFAULT_LIMIT,
    DEFAULT_PAGE,
    MAX_DAYS,
    MAX_PAGE_NUMBER,
    MAX_PAGE_SIZE,
    MAX_QUERY_LENGTH,
    MAX_ROLE_EVENTS,
)
from azurerbac.web.dependencies import PagesDeps, get_pages_deps
from azurerbac.web.routes.models import OperationWithCount
from azurerbac.web.services.models import (
    DataActionFilter,
    OperationSearchParams,
    OperationSortField,
    PaginationInfo,
)
from azurerbac.web.services.pages import (
    add_role_counts,
    build_role_redirect_url,
    compute_related_roles,
    compute_role_comparison,
    compute_role_effective_permissions,
    enrich_event_with_diff,
    filter_operations,
    get_role_from_cache_or_db,
    get_roles_allowing_operation,
    sort_operations,
)
from azurerbac.web.utils import role_json_pretty

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])


# --- Compare routes MUST be registered before /roles/{role_id} to avoid
#     FastAPI matching "compare" as a UUID path parameter. ---


@router.api_route(
    "/roles/compare",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    name="compare",
)
async def compare(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """Compare roles landing page with popular comparisons and role picker."""
    popular = deps.app_cache.get_popular_comparisons()

    # Build list of all active roles for the picker (sorted by name)
    all_roles = [
        {"role_id": r.role_id, "role_name": r.role_name}
        for r in sorted(deps.app_cache.get_all_roles(), key=lambda r: r.role_name.lower())
    ]

    return deps.templates.TemplateResponse(
        request,
        "compare.html",
        {
            "popular_comparisons": popular,
            "all_roles": all_roles,
            "total_roles": len(all_roles),
        },
    )


@router.api_route(
    "/roles/compare/{role_a_id}/{role_b_id}",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    name="compare_roles",
)
async def compare_roles(
    request: Request,
    role_a_id: uuid.UUID,
    role_b_id: uuid.UUID,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """Compare two roles side by side."""
    if role_a_id == role_b_id:
        raise HTTPException(status_code=400, detail="Cannot compare a role with itself")

    comparison = compute_role_comparison(str(role_a_id), str(role_b_id), cache=deps.app_cache)
    if comparison is None:
        raise HTTPException(status_code=404)

    return deps.templates.TemplateResponse(
        request,
        "compare_detail.html",
        {"comparison": comparison},
    )


@router.api_route(
    "/roles/{role_id}",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    name="role_detail",
)
@router.api_route(
    "/roles/{role_id}/{slug}",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    name="role_detail_slug",
)
async def role_detail(
    request: Request,
    role_id: uuid.UUID,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    slug: str | None = None,
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE_NUMBER)] = DEFAULT_PAGE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_LIMIT,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
) -> Response:
    """Role detail page."""
    # Convert to canonical string format (with dashes)
    role_id_str = str(role_id)

    # Get role from cache or database
    result = await get_role_from_cache_or_db(
        deps.SessionLocal,
        deps.Role,
        deps.RoleHistory,
        role_id_str,
        max_events=MAX_ROLE_EVENTS,
    )

    if result.cached_role is None:
        raise HTTPException(status_code=404)

    role = result.cached_role
    role_def = result.definition

    # SEO: Get expected slug for canonical URL
    role_name = getattr(role, "role_name", "") or (role_def.role_name if role_def else "")
    expected_slug = slugify(role_name)

    # If no slug provided, serve content directly (helps GUID-based searches)
    if slug is not None and slug != expected_slug:
        url = build_role_redirect_url(
            request,
            role_id_str,
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
    effective_perms = compute_role_effective_permissions(role_def, all_ops) if role_def else None
    related_roles = compute_related_roles(role_id_str, cache=deps.app_cache) if role_def else []

    return deps.templates.TemplateResponse(
        request,
        "role.html",
        {
            "role": role,
            "events": enriched,
            "first_scan": result.first_scan,
            "role_json_pretty": role_json_pretty(display_json),
            "effective_perms": effective_perms,
            "related_roles": related_roles,
            "q": q,
            "page": page,
            "limit": limit,
            "days": days,
        },
    )


@router.api_route("/operations", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def operations_list(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE_NUMBER)] = DEFAULT_PAGE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_LIMIT,
    is_data_action: str | None = None,
    provider: str | None = None,
    sort: str = OperationSortField.NAME,
    order: str = SortOrder.ASC,
) -> Response:
    """Operations list page."""
    logger.info("Operations list: q='%s' page=%d provider=%s", q or "", page, provider or "all")

    # Get all operations from cache
    all_operations = deps.app_cache.get_all_operations()
    total_operations = len(all_operations)

    # Get unique providers for filter dropdown (precomputed during cache build)
    providers = deps.app_cache.cache.unique_providers

    # Parse is_data_action filter
    is_data_action_filter = DataActionFilter.parse(is_data_action)

    # Build cache key from filter parameters (exclude empty values for cleaner keys)
    filter_parts = [
        p
        for p in [
            f"q={q}" if q else "",
            f"da={is_data_action_filter}" if is_data_action_filter is not None else "",
            f"p={provider}" if provider else "",
        ]
        if p
    ]
    filter_str = ",".join(filter_parts) if filter_parts else "all"
    cache_key = f"ops:{filter_str}:{sort}:{order}:{page}:{limit}"
    count_key = f"ops_count:{filter_str}"

    # Try cache first (short-circuits on first miss)
    if (cached_page := deps.app_cache.get_operation_page(cache_key)) is not None and (
        cached_count := deps.app_cache.get_operation_page(count_key)
    ) is not None:
        # Cache hit - use cached data
        page_operations = cached_page
        total_filtered = cached_count
        pagination = PaginationInfo.compute(total_filtered, page, limit)
    else:
        # Cache miss - compute and cache
        search_params = OperationSearchParams(
            query=q,
            is_data_action=is_data_action_filter,
            provider=provider,
            sort=sort,
            order=order,
        )
        filtered_ops = filter_operations(all_operations, search_params)
        sorted_ops = sort_operations(filtered_ops, sort, order, cache=deps.app_cache)

        total_filtered = len(sorted_ops)
        pagination = PaginationInfo.compute(total_filtered, page, limit)
        page_slice = sorted_ops[pagination.start_idx : pagination.end_idx]

        # Convert to typed models with role counts (only for paginated slice)
        page_operations = [
            OperationWithCount.from_operation(op, role_count)
            for op, role_count in add_role_counts(page_slice, cache=deps.app_cache)
        ]

        # Cache the results
        deps.app_cache.set_operation_page(cache_key, page_operations)
        deps.app_cache.set_operation_page(count_key, total_filtered)

    return deps.templates.TemplateResponse(
        request,
        "operations.html",
        {
            "request": request,
            "operations": page_operations,
            "total_operations": total_operations,
            "total_filtered": total_filtered,
            "page": page,
            "limit": limit,
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


@router.api_route(
    "/operations/{operation_name:path}",
    methods=["GET", "HEAD"],
    response_class=HTMLResponse,
    name="operation_detail",
)
async def operation_detail(
    operation_name: str,
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE_NUMBER)] = DEFAULT_PAGE,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_LIMIT,
    is_data_action: str | None = None,
    provider: str | None = None,
) -> Response:
    """Operation detail page."""
    # URL decode the operation name (handles both encoded and unencoded paths)
    decoded_name = unquote(operation_name)

    # Parse is_data_action filter for template
    is_data_action_filter = DataActionFilter.parse(is_data_action)

    # Find the operation by name using the indexed lookup
    operation = deps.app_cache.cache.ops_by_name_lower.get(decoded_name.lower())

    if not operation:
        raise HTTPException(status_code=404)

    # Find roles that allow this operation
    allowing_roles = get_roles_allowing_operation(operation.name, operation.is_data_action)

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


@router.api_route("/recommend", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def recommend_page(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """Role recommender page."""
    logger.info("Recommend page loaded")
    ops_count = deps.app_cache.cache.metadata.operations_count

    return deps.templates.TemplateResponse(
        request,
        "recommend.html",
        {
            "operations_loaded": ops_count > 0,
            "operations_count": ops_count,
        },
    )


@router.api_route("/about", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def about_page(
    request: Request,
    deps: Annotated[PagesDeps, Depends(get_pages_deps)],
) -> Response:
    """About page."""
    logger.info("About page loaded")
    # Preserve ai=1 parameter if set
    ai_mode = request.query_params.get("ai") == "1"

    roles_count = deps.app_cache.cache.active_roles_count
    ops_count = deps.app_cache.cache.metadata.operations_count

    return deps.templates.TemplateResponse(
        request,
        "about.html",
        {
            "total_roles": roles_count,
            "total_operations": ops_count,
            "ai_mode": ai_mode,
        },
    )
