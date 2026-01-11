"""Web services package.

This package contains business logic separated from route handlers.
"""

from azurerbac.core.enums import EventTypeFilter, SortOrder, StatusFilter
from azurerbac.web.services.dashboard import (
    PaginationParams,
    SortField,
    enrich_role_with_counts,
    ensure_scan_metadata,
    fetch_events_from_db,
    fetch_roles_paginated,
    filter_cached_events,
    get_common_dashboard_data,
    search_roles,
    search_roles_in_cache,
    search_roles_in_db,
)
from azurerbac.web.services.models import (
    DashboardSummary,
    EnrichedChangeEvent,
    PaginatedResult,
    PaginationInfo,
    PatternMatchResult,
    RoleAllowingOperation,
    RoleDetailResult,
    RoleEffectivePermissions,
    RoleWithCounts,
    ScanMetadata,
)
from azurerbac.web.services.pages import (
    DataActionFilter,
    OperationSortField,
    compute_role_effective_permissions,
    get_roles_allowing_operation,
)
from azurerbac.web.services.startup import (
    cache_refresh_task,
    ensure_db,
    preload_cache,
)

__all__ = [
    "DashboardSummary",
    "DataActionFilter",
    "EnrichedChangeEvent",
    "EventTypeFilter",
    "OperationSortField",
    "PaginatedResult",
    "PaginationInfo",
    "PaginationParams",
    "PatternMatchResult",
    "RoleAllowingOperation",
    "RoleDetailResult",
    "RoleEffectivePermissions",
    "RoleWithCounts",
    "ScanMetadata",
    "SortField",
    "SortOrder",
    "StatusFilter",
    "cache_refresh_task",
    "compute_role_effective_permissions",
    "enrich_role_with_counts",
    "ensure_db",
    "ensure_scan_metadata",
    "fetch_events_from_db",
    "fetch_roles_paginated",
    "filter_cached_events",
    "get_common_dashboard_data",
    "get_roles_allowing_operation",
    "preload_cache",
    "search_roles",
    "search_roles_in_cache",
    "search_roles_in_db",
]
