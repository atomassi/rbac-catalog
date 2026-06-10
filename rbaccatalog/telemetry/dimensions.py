"""Canonical values for custom-metric dimensions emitted to Azure Monitor.

The string values are an external contract: Grafana/Application Insights
dashboards group and filter ``customDimensions`` by them, so don't change a
value without updating the dashboards.
"""

from __future__ import annotations

from enum import StrEnum


class CacheType(StrEnum):
    """``cache_type`` dimension for the ``cache_access`` metric."""

    ROLE_BY_ID = "role_by_id"
    ROLE_COVERAGE = "role_coverage"
    ROLE_NET_PERMISSIONS = "role_net_permissions"
    OPERATION_TO_ROLES = "operation_to_roles"
    FILTERED_EVENTS = "filtered_events"
    ROLE_PAGE = "role_page"
    ROLE_PAGE_COUNT = "role_page_count"
    OPERATION_PAGE = "operation_page"
    OPERATION_PAGE_COUNT = "operation_page_count"
    ALLOWING_ROLES = "allowing_roles"
    RELATED_ROLES = "related_roles"
    COMPARISONS = "comparisons"
    EFFECTIVE_PERMS = "effective_perms"


class DbFallbackType(StrEnum):
    """``type`` dimension for the ``db_fallback`` metric."""

    RECENT_CHANGES = "recent_changes"
    SCAN_METADATA = "scan_metadata"
    ROLE_DETAIL = "role_detail"


class MCPRateLimitType(StrEnum):
    """``type`` dimension for the ``mcp_rate_limit`` metric."""

    MAX_SESSIONS = "max_sessions"
    GLOBAL = "global"
    SESSION = "session"


class DbQueryName(StrEnum):
    """``query`` dimension for the ``db_query`` metrics."""

    FETCH_ALL_ROLES = "fetch_all_roles"
    FETCH_ALL_OPERATIONS = "fetch_all_operations"
    FETCH_ALL_HISTORY_EVENTS = "fetch_all_history_events"
    FETCH_SCAN_STATUS = "fetch_scan_status"
    FETCH_RECENT_CHANGES = "fetch_recent_changes"
    FETCH_LAST_SCAN = "fetch_last_scan"
    FETCH_FIRST_SCAN = "fetch_first_scan"
    FETCH_ROLE_BY_ID = "fetch_role_by_id"
    FETCH_FIRST_SCAN_TIMESTAMP = "fetch_first_scan_timestamp"
    FETCH_ROLE_HISTORY = "fetch_role_history"
