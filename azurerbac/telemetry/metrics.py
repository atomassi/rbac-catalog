"""Application Insights custom metrics using OpenTelemetry."""

from __future__ import annotations

import logging
from typing import Any

from azurerbac.settings import Settings
from azurerbac.telemetry.sender import MetricsSender

logger = logging.getLogger(__name__)

__all__ = [
    "flush_metrics",
    "track_ai_recommendation",
    "track_cache_call",
    "track_cache_hit",
    "track_cache_refresh",
    "track_cache_refresh_failure",
    "track_db_fallback",
    "track_db_query",
    "track_duration",
    "track_event",
    "track_gauge",
    "track_metric",
    "track_operations_scan",
    "track_role_recommendation",
    "track_role_scan",
    "track_startup",
    "track_worker_result",
]


def _metrics_enabled() -> bool:
    settings = Settings.get()
    return settings.is_deployed and bool(settings.app_insights_connection_string)


def flush_metrics(timeout_ms: int | None = None) -> bool:
    """Force flush all pending metrics to Azure Monitor.

    Call this before process exit or when you need metrics exported immediately.
    For long-running web apps, this is typically not needed.

    Args:
        timeout_ms: Timeout in milliseconds for the flush operation.
                   Defaults to TELEMETRY_FLUSH_TIMEOUT_MS (10000ms).

    Returns:
        True if flush succeeded, False otherwise.
    """
    if timeout_ms is None:
        from azurerbac.telemetry.sender import TELEMETRY_FLUSH_TIMEOUT_MS

        timeout_ms = TELEMETRY_FLUSH_TIMEOUT_MS
    return MetricsSender.flush(timeout_ms)


def track_metric(
    name: str,
    value: float,
    properties: dict[str, Any] | None = None,
) -> None:
    """Track a custom metric to Application Insights."""
    # Always log locally (even when local, for debugging)
    props_str = f" {properties}" if properties else ""
    logger.debug("Metric: %s=%s%s", name, value, props_str)

    if not _metrics_enabled():
        return

    try:
        MetricsSender.send_gauge(name, value, properties)
    except ImportError:
        pass  # OpenTelemetry not available
    except Exception as e:
        logger.exception("Failed to track metric %s: %s", name, e)


def track_gauge(name: str, value: float, properties: dict[str, Any] | None = None) -> None:
    """Track a gauge metric (current value/state).

    Use for: counts, sizes, current state values where only the latest matters.
    """
    props_str = f" {properties}" if properties else ""
    logger.debug("Gauge: %s=%s%s", name, value, props_str)

    if not _metrics_enabled():
        return

    try:
        MetricsSender.send_gauge(name, value, properties)
    except Exception as e:
        logger.exception("Failed to track gauge %s: %s", name, e)


def track_duration(
    name: str, duration_seconds: float, properties: dict[str, Any] | None = None
) -> None:
    """Track a duration/latency metric using a histogram.

    Use for: startup time, cache refresh time, scan duration.
    Histograms provide min, max, avg, count, and percentiles (p50, p95, p99).
    """
    props_str = f" {properties}" if properties else ""
    logger.debug("Duration: %s=%.3fs%s", name, duration_seconds, props_str)

    if not _metrics_enabled():
        return

    try:
        MetricsSender.send_histogram(name, duration_seconds, properties)
    except Exception as e:
        logger.exception("Failed to track duration %s: %s", name, e)


def track_event(name: str, properties: dict[str, Any] | None = None) -> None:
    """Track an event occurrence (counter).

    Use for: startup events, scan events, refresh events.
    Each call increments the counter by 1.
    """
    props_str = f" {properties}" if properties else ""
    logger.debug("Event: %s%s", name, props_str)

    if not _metrics_enabled():
        return

    try:
        MetricsSender.send_count(name, 1, properties)
    except Exception as e:
        logger.exception("Failed to track event %s: %s", name, e)


def track_db_fallback(fallback_type: str, reason: str, key: str | None = None) -> None:
    """Track when the application falls back to database due to cache miss.

    Args:
        fallback_type: Type of data being fetched (e.g., "role_detail", "role_history")
        reason: Reason for fallback (e.g., "cache_miss", "not_in_cache")
        key: Optional identifier (e.g., role_id) for local debugging only
    """
    key_str = f" key={key}" if key else ""
    logger.debug("DB fallback: type=%s reason=%s%s", fallback_type, reason, key_str)

    if not _metrics_enabled():
        return

    try:
        props: dict[str, Any] = {"type": fallback_type, "reason": reason}
        track_event("db_fallback", props)
    except Exception as e:
        logger.exception("Failed to track db fallback: %s", e)


def track_cache_call(method: str, **kwargs: Any) -> None:
    """Track cache method calls for dashboard analytics.

    Args:
        method: Cache method name (e.g., "get_role_by_id", "search_operations")
        **kwargs: Additional context (e.g., role_id, query)
    """
    if kwargs:
        details = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        logger.debug("Cache call: %s (%s)", method, details)
    else:
        logger.debug("Cache call: %s", method)

    if not _metrics_enabled():
        return

    try:
        props: dict[str, Any] = {"method": method}
        track_event("cache_call", props)
    except Exception as e:
        logger.exception("Failed to track cache call: %s", e)


def track_ai_recommendation(
    mode: str,
    result_count: int,
    is_error: bool = False,
) -> None:
    """Track AI recommendation requests for dashboard analytics.

    Args:
        mode: Recommender mode used (tfidf, semantic, colbert, llm, etc.)
        result_count: Number of recommendations returned
        is_error: Whether the request failed
    """
    status = "error" if is_error else "success"
    logger.info(
        "AI recommendation: mode=%s results=%d status=%s",
        mode,
        result_count,
        status,
    )

    if not _metrics_enabled():
        return

    try:
        props: dict[str, Any] = {
            "mode": mode,
            "status": status,
            "result_count": result_count,
        }
        track_event("ai_recommendation", props)
    except Exception as e:
        logger.exception("Failed to track AI recommendation: %s", e)


def track_role_recommendation(
    operations_count: int,
    expanded_count: int,
    result_count: int,
    duration_seconds: float | None = None,
) -> None:
    """Track operation-based role recommendations for dashboard analytics.

    Args:
        operations_count: Number of operations requested by user
        expanded_count: Number of operations after wildcard expansion
        result_count: Number of matching roles returned
        duration_seconds: Optional execution time
    """
    logger.info(
        "Role recommendation: ops=%d expanded=%d results=%d",
        operations_count,
        expanded_count,
        result_count,
    )

    if not _metrics_enabled():
        return

    try:
        props: dict[str, Any] = {
            "operations_count": operations_count,
            "expanded_count": expanded_count,
            "result_count": result_count,
        }
        track_event("role_recommendation", props)
        if duration_seconds is not None:
            track_duration("role_recommendation_duration_seconds", duration_seconds, props)
    except Exception as e:
        logger.exception("Failed to track role recommendation: %s", e)


def track_startup(duration_seconds: float, roles_count: int, operations_count: int) -> None:
    """Track application startup metrics.

    Args:
        duration_seconds: Total startup time in seconds
        roles_count: Number of roles loaded
        operations_count: Number of operations loaded
    """
    if not _metrics_enabled():
        logger.debug("Skipping track_startup: metrics disabled")
        return

    try:
        track_duration("startup_duration_seconds", duration_seconds)
        track_gauge("startup_roles_count", roles_count)
        track_gauge("startup_operations_count", operations_count)
        track_event("startup_event")
        logger.info(
            f"Tracked startup: {duration_seconds:.2f}s, "
            f"{roles_count} roles, {operations_count} operations"
        )
    except Exception as e:
        logger.exception("Failed to track startup metric: %s", e)


def track_cache_refresh(
    duration_seconds: float,
    source: str,
    roles_count: int,
    operations_count: int,
) -> None:
    """Track cache refresh metrics.

    Args:
        duration_seconds: Time taken to refresh cache
        source: What triggered the refresh ("startup", "worker", "periodic", "manual")
        roles_count: Number of roles after refresh
        operations_count: Number of operations after refresh
    """
    if not _metrics_enabled():
        logger.debug("Skipping track_cache_refresh: metrics disabled")
        return

    try:
        track_duration("cache_refresh_duration_seconds", duration_seconds, {"source": source})
        track_gauge("cache_refresh_roles_count", roles_count, {"source": source})
        track_gauge("cache_refresh_operations_count", operations_count, {"source": source})
        track_event("cache_refresh_event", {"source": source})
        logger.info(
            f"Tracked cache refresh ({source}): {duration_seconds:.2f}s, "
            f"{roles_count} roles, {operations_count} operations"
        )
    except Exception as e:
        logger.exception("Failed to track cache refresh metric: %s", e)


def track_role_scan(
    roles_fetched: int,
    roles_added: int,
    roles_updated: int,
    roles_deleted: int,
) -> None:
    """Track role scan metrics.

    Duration is tracked separately by the worker via track_worker_result.

    Args:
        roles_fetched: Total roles fetched from Azure
        roles_added: New roles added
        roles_updated: Existing roles updated
        roles_deleted: Roles marked as deleted
    """
    if not _metrics_enabled():
        logger.debug("Skipping track_role_scan: metrics disabled")
        return

    try:
        track_gauge("role_scan_fetched", roles_fetched)
        track_gauge("role_scan_added", roles_added)
        track_gauge("role_scan_updated", roles_updated)
        track_gauge("role_scan_deleted", roles_deleted)
        total_changes = roles_added + roles_updated + roles_deleted
        track_gauge("role_scan_total_changes", total_changes)
        track_event("role_scan_event")
        logger.info(
            f"Tracked role scan: fetched={roles_fetched}, added={roles_added}, "
            f"updated={roles_updated}, deleted={roles_deleted}"
        )
    except Exception as e:
        logger.exception("Failed to track role scan metric: %s", e)


def track_operations_scan(operations_count: int) -> None:
    """Track operations scan metrics.

    Duration is tracked separately by the worker via track_worker_result.

    Args:
        operations_count: Total operations fetched
    """
    if not _metrics_enabled():
        logger.debug("Skipping track_operations_scan: metrics disabled")
        return

    try:
        track_gauge("operations_scan_count", operations_count)
        track_event("operations_scan_event")
        logger.info(f"Tracked operations scan: {operations_count} operations")
    except Exception as e:
        logger.exception("Failed to track operations scan metric: %s", e)


def track_worker_result(
    operation_name: str,
    result: str,
    duration_seconds: float | None = None,
    error_message: str | None = None,
) -> None:
    """Track the result of a worker operation (success/failure).

    Args:
        operation_name: Name of the operation (e.g., "role-scan", "operations-scan")
        result: "success" or "failure"
        duration_seconds: Optional duration of the operation
        error_message: Optional error message if result is "failure"
    """
    props = {"operation": operation_name, "result": result}

    parts = [f"Worker op={operation_name} result={result}"]
    if duration_seconds:
        parts.append(f"duration={duration_seconds:.2f}s")
    if error_message:
        parts.append(f"error={error_message[:100]}")
    logger.info(", ".join(parts))

    if not _metrics_enabled():
        logger.debug("Skipping track_worker_result: metrics disabled")
        return

    try:
        # Track as counter (1 for each operation completed)
        track_event("worker_operation_result", props)

        # Also track success/failure separately for easy dashboard counts
        if result == "success":
            track_event("worker_operation_success", {"operation": operation_name})
        else:
            track_event("worker_operation_failure", {"operation": operation_name})

        if duration_seconds is not None:
            track_duration(
                "worker_operation_duration_seconds",
                duration_seconds,
                {"operation": operation_name, "result": result},
            )
    except Exception as e:
        logger.exception("Failed to track worker result: %s", e)


def track_db_query(
    query_name: str,
    duration_seconds: float,
    rows_affected: int | None = None,
) -> None:
    """Track database query metrics.

    Args:
        query_name: Name of the query (e.g., "fetch_roles", "fetch_operations")
        duration_seconds: Time taken to execute the query in seconds
        rows_affected: Optional number of rows returned/affected
    """
    props: dict[str, Any] = {"query": query_name}
    if rows_affected is not None:
        props["rows"] = rows_affected

    rows_str = f" rows={rows_affected}" if rows_affected is not None else ""
    logger.debug("DB query: %s (%.3fs)%s", query_name, duration_seconds, rows_str)

    if not _metrics_enabled():
        return

    try:
        track_duration("db_query_duration_seconds", duration_seconds, props)
        track_event("db_query_event", props)
    except Exception as e:
        logger.exception("Failed to track db query: %s", e)


def track_cache_hit(
    cache_type: str,
    hit: bool,
    key: str | None = None,
) -> None:
    """Track cache hit/miss events.

    Args:
        cache_type: Type of cache (e.g., "role", "operation", "role_page")
        hit: True if cache hit, False if cache miss
        key: Optional key being looked up (for debugging)
    """
    key_str = f" key={key}" if key else ""
    logger.debug("Cache %s: %s%s", "hit" if hit else "miss", cache_type, key_str)

    if not _metrics_enabled():
        logger.debug("Skipping track_cache_hit: metrics disabled")
        return

    try:
        props: dict[str, Any] = {"cache_type": cache_type, "hit": hit}
        track_event("cache_access", props)
    except Exception as e:
        logger.exception("Failed to track cache hit: %s", e)


def track_cache_refresh_failure(
    source: str,
    reason: str,
) -> None:
    """Track cache refresh failure.

    Args:
        source: What triggered the refresh ("worker", "periodic")
        reason: Reason for failure
    """
    logger.warning("Cache refresh failed (%s): %s", source, reason)

    if not _metrics_enabled():
        logger.debug("Skipping track_cache_refresh_failure: metrics disabled")
        return

    try:
        track_event("cache_refresh_failure", {"source": source, "reason": reason})
    except Exception as e:
        logger.exception("Failed to track cache refresh failure: %s", e)
