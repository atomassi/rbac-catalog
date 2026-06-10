"""Canonical names for custom metrics emitted to Azure Monitor.

The string values are an external contract: Grafana/Application Insights
dashboards query ``customMetrics`` by these names, so don't change a value
without updating the dashboards.
"""

from __future__ import annotations

from enum import StrEnum


class MetricName(StrEnum):
    """Stable identifiers for every custom metric the app emits."""

    # Application startup (cold start)
    STARTUP_DURATION_SECONDS = "startup_duration_seconds"
    STARTUP_EVENT = "startup_event"

    # In-memory cache / database fallbacks
    DB_FALLBACK = "db_fallback"
    IN_MEMORY_CACHE_CALL = "cache_call"
    IN_MEMORY_CACHE_ACCESS = "cache_access"
    IN_MEMORY_CACHE_REFRESH_FAILURE = "cache_refresh_failure"

    # AI and operation-based recommendations
    AI_RECOMMENDATION = "ai_recommendation"
    AI_RECOMMENDATION_ERROR = "ai_recommendation_error"
    ROLE_RECOMMENDATION = "role_recommendation"
    ROLE_RECOMMENDATION_DURATION_SECONDS = "role_recommendation_duration_seconds"

    # In-memory cache refresh
    IN_MEMORY_CACHE_REFRESH_DURATION_SECONDS = "cache_refresh_duration_seconds"
    IN_MEMORY_CACHE_REFRESH_EVENT = "cache_refresh_event"

    # Role scan
    ROLE_SCAN_FETCHED = "role_scan_fetched"
    ROLE_SCAN_ADDED = "role_scan_added"
    ROLE_SCAN_UPDATED = "role_scan_updated"
    ROLE_SCAN_DELETED = "role_scan_deleted"
    ROLE_SCAN_EVENT = "role_scan_event"

    # Operations scan
    OPERATIONS_SCAN_COUNT = "operations_scan_count"
    OPERATIONS_SCAN_EVENT = "operations_scan_event"

    # Background worker operations
    WORKER_OPERATION_RESULT = "worker_operation_result"
    WORKER_OPERATION_DURATION_SECONDS = "worker_operation_duration_seconds"

    # Database queries
    DB_QUERY_DURATION_SECONDS = "db_query_duration_seconds"
    DB_QUERY_EVENT = "db_query_event"
    DB_QUERY_ERROR_DURATION_SECONDS = "db_query_error_duration_seconds"
    DB_QUERY_ERROR_EVENT = "db_query_error_event"

    # MCP server
    MCP_SERVER_INITIALIZED = "mcp_server_initialized"
    MCP_TOOL_CALL = "mcp_tool_call"
    MCP_TOOL_DURATION_SECONDS = "mcp_tool_duration_seconds"
    MCP_TOOL_RESULT_COUNT = "mcp_tool_result_count"
    MCP_RATE_LIMIT = "mcp_rate_limit"
