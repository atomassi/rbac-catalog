"""Web services package.

This package contains business logic separated from route handlers.
"""

from azurerbac.web.services.cache import (
    get_all_operations,
    get_all_role_jsons,
    get_operations_for_recommender,
)
from azurerbac.web.services.dashboard import (
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
from azurerbac.web.services.pages import (
    compute_role_effective_permissions,
    get_roles_allowing_operation,
    get_unique_providers,
)
from azurerbac.web.services.startup import (
    cache_refresh_task,
    ensure_db,
    preload_cache,
)

__all__ = [
    "cache_refresh_task",
    "compute_role_effective_permissions",
    "enrich_role_with_counts",
    "ensure_db",
    "ensure_scan_metadata",
    "fetch_events_from_db",
    "fetch_roles_paginated",
    "filter_cached_events",
    "get_all_operations",
    "get_all_role_jsons",
    "get_common_dashboard_data",
    "get_operations_for_recommender",
    "get_roles_allowing_operation",
    "get_unique_providers",
    "preload_cache",
    "search_roles",
    "search_roles_in_cache",
    "search_roles_in_db",
]
