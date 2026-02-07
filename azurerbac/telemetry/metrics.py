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
    "track_db_query_error",
    "track_duration",
    "track_event",
    "track_gauge",
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
    """Force flush all pending metrics to Azure Monitor. Call before process exit."""
    if timeout_ms is None:
        from azurerbac.telemetry.sender import TELEMETRY_FLUSH_TIMEOUT_MS

        timeout_ms = TELEMETRY_FLUSH_TIMEOUT_MS
    return MetricsSender.flush(timeout_ms)


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
    """Track database fallback due to cache miss."""
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
    """Track cache method calls for dashboard analytics."""
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
    """Track AI recommendation requests for dashboard analytics."""
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
    """Track operation-based role recommendations for dashboard analytics."""
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
    """Track application startup metrics."""
    if not _metrics_enabled():
        logger.debug("Skipping track_startup: metrics disabled")
        return

    try:
        track_duration("startup_duration_seconds", duration_seconds)
        track_gauge("startup_roles_count", roles_count)
        track_gauge("startup_operations_count", operations_count)
        track_event("startup_event")
        logger.info(
            "Tracked startup: %.2fs, %d roles, %d operations",
            duration_seconds,
            roles_count,
            operations_count,
        )
    except Exception as e:
        logger.exception("Failed to track startup metric: %s", e)


def track_cache_refresh(
    duration_seconds: float,
    source: str,
    roles_count: int,
    operations_count: int,
) -> None:
    """Track cache refresh metrics."""
    if not _metrics_enabled():
        logger.debug("Skipping track_cache_refresh: metrics disabled")
        return

    try:
        track_duration("cache_refresh_duration_seconds", duration_seconds, {"source": source})
        track_gauge("cache_refresh_roles_count", roles_count, {"source": source})
        track_gauge("cache_refresh_operations_count", operations_count, {"source": source})
        track_event("cache_refresh_event", {"source": source})
        logger.info(
            "Tracked cache refresh (%s): %.2fs, %d roles, %d operations",
            source,
            duration_seconds,
            roles_count,
            operations_count,
        )
    except Exception as e:
        logger.exception("Failed to track cache refresh metric: %s", e)


def track_role_scan(
    roles_fetched: int,
    roles_added: int,
    roles_updated: int,
    roles_deleted: int,
) -> None:
    """Track role scan metrics. Duration tracked separately via track_worker_result."""
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
            "Tracked role scan: fetched=%d, added=%d, updated=%d, deleted=%d",
            roles_fetched,
            roles_added,
            roles_updated,
            roles_deleted,
        )
    except Exception as e:
        logger.exception("Failed to track role scan metric: %s", e)


def track_operations_scan(operations_count: int) -> None:
    """Track operations scan metrics. Duration tracked separately via track_worker_result."""
    if not _metrics_enabled():
        logger.debug("Skipping track_operations_scan: metrics disabled")
        return

    try:
        track_gauge("operations_scan_count", operations_count)
        track_event("operations_scan_event")
        logger.info("Tracked operations scan: %d operations", operations_count)
    except Exception as e:
        logger.exception("Failed to track operations scan metric: %s", e)


def track_worker_result(
    operation_name: str,
    result: str,
    duration_seconds: float | None = None,
    error_message: str | None = None,
) -> None:
    """Track the result of a worker operation (success/failure)."""
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
    """Track database query metrics."""
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


def track_db_query_error(
    query_name: str,
    duration_seconds: float,
    error_type: str,
) -> None:
    """Track failed database query metrics."""
    props: dict[str, Any] = {"query": query_name, "error_type": error_type}

    logger.warning("DB query failed: %s (%.3fs) error=%s", query_name, duration_seconds, error_type)

    if not _metrics_enabled():
        return

    try:
        track_duration("db_query_error_duration_seconds", duration_seconds, props)
        track_event("db_query_error_event", props)
    except Exception as e:
        logger.exception("Failed to track db query error: %s", e)


def track_cache_hit(
    cache_type: str,
    hit: bool,
    key: str | None = None,
) -> None:
    """Track cache hit/miss events."""
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
    """Track cache refresh failure."""
    logger.warning("Cache refresh failed (%s): %s", source, reason)

    if not _metrics_enabled():
        logger.debug("Skipping track_cache_refresh_failure: metrics disabled")
        return

    try:
        track_event("cache_refresh_failure", {"source": source, "reason": reason})
    except Exception as e:
        logger.exception("Failed to track cache refresh failure: %s", e)
