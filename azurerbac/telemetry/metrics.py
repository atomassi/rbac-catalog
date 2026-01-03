"""Application Insights custom metrics using OpenTelemetry.

This module provides helpers to send custom metrics to Azure Application Insights.
Metrics are only sent in production (when WEBSITE_SITE_NAME is set).

Metrics appear in the `customMetrics` table in Application Insights.
Query example: customMetrics | where name == "startup_duration_seconds"

Usage:
    from azurerbac.telemetry import track_metric, track_cache_stats

    # Track a single metric
    track_metric("cache_hit_rate", 0.95, {"cache_type": "roles"})

    # Track cache statistics
    track_cache_stats(app_cache)

OpenTelemetry provides:
- Histograms with percentiles (p50, p95, p99) for durations
- Gauges for current values
- Counters for event counts
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from azurerbac.settings import Settings, is_running_in_azure
from azurerbac.telemetry.sender import MetricsSender

if TYPE_CHECKING:
    from azurerbac.cache import AppCache

logger = logging.getLogger(__name__)


class BaseTimer(ABC):
    """Abstract base class for timing context managers.

    Eliminates duplication between TimedDbQuery and TimedOperation.
    Subclasses implement _on_exit to handle timing results.
    """

    __slots__ = ("_start_time",)

    def __init__(self) -> None:
        self._start_time: float = 0

    def __enter__(self) -> Self:
        self._start_time = time.time()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._on_exit(time.time() - self._start_time, exc_type)

    async def __aenter__(self) -> Self:
        self._start_time = time.time()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._on_exit(time.time() - self._start_time, exc_type)

    @abstractmethod
    def _on_exit(self, elapsed: float, exc_type: type[BaseException] | None) -> None:
        """Handle timing result. Called on both sync and async exit."""


def _metrics_enabled() -> bool:
    return is_running_in_azure() and bool(Settings.get().app_insights_connection_string)


def flush_metrics(timeout_ms: int | None = None) -> bool:
    """Force flush all pending metrics to Azure Monitor.

    Call this before process exit or when you need metrics exported immediately.
    For long-running web apps, this is typically not needed.

    Args:
        timeout_ms: Timeout in milliseconds for the flush operation.
                   Defaults to settings.telemetry_flush_timeout_ms.

    Returns:
        True if flush succeeded, False otherwise.
    """
    if timeout_ms is None:
        timeout_ms = Settings.get().telemetry_flush_timeout_ms
    return MetricsSender.flush(timeout_ms)


def track_metric(
    name: str,
    value: float,
    properties: dict[str, Any] | None = None,
) -> None:
    """Track a custom metric to Application Insights.

    Metrics are sent to the `customMetrics` table.
    Query in Log Analytics: customMetrics | where name == "metric_name"

    Also logs the metric locally for debugging.

    Args:
        name: Metric name (e.g., "cache_hit_rate", "roles_count")
        value: Numeric value
        properties: Optional dimension properties (e.g., {"cache_type": "roles"})

    Note: No-op when running locally or without App Insights configured.
          Use track_gauge, track_duration, or track_event for specific metric types.
    """
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


def track_cache_stats(app_cache: AppCache) -> None:
    """Track cache statistics as custom metrics.

    Args:
        app_cache: The AppCache instance to get stats from.
    """
    if not _metrics_enabled():
        return

    try:
        track_gauge("cache_roles_count", len(app_cache.cache.roles_by_id))
        track_gauge("cache_operations_count", len(app_cache.get_all_operations()))
        track_gauge("cache_events_count", len(app_cache.cache.all_change_events))
        track_gauge("cache_entries_count", app_cache.get_role_pages_count())
    except Exception as e:
        logger.exception("Failed to track cache stats: %s", e)


def track_startup(duration_seconds: float, roles_count: int, operations_count: int) -> None:
    """Track application startup metrics.

    Args:
        duration_seconds: Total startup time in seconds
        roles_count: Number of roles loaded
        operations_count: Number of operations loaded
    """
    if not _metrics_enabled():
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
    duration_seconds: float,
    roles_fetched: int,
    roles_added: int,
    roles_updated: int,
    roles_deleted: int,
) -> None:
    """Track role scan metrics.

    Args:
        duration_seconds: Time taken to complete the scan
        roles_fetched: Total roles fetched from Azure
        roles_added: New roles added
        roles_updated: Existing roles updated
        roles_deleted: Roles marked as deleted
    """
    if not _metrics_enabled():
        return

    try:
        track_duration("role_scan_duration_seconds", duration_seconds)
        track_gauge("role_scan_fetched", roles_fetched)
        track_gauge("role_scan_added", roles_added)
        track_gauge("role_scan_updated", roles_updated)
        track_gauge("role_scan_deleted", roles_deleted)
        total_changes = roles_added + roles_updated + roles_deleted
        track_gauge("role_scan_total_changes", total_changes)
        track_event("role_scan_event")
        logger.info(
            f"Tracked role scan: {duration_seconds:.2f}s, "
            f"fetched={roles_fetched}, added={roles_added}, "
            f"updated={roles_updated}, deleted={roles_deleted}"
        )
    except Exception as e:
        logger.exception("Failed to track role scan metric: %s", e)


def track_operations_scan(duration_seconds: float, operations_count: int) -> None:
    """Track operations scan metrics.

    Args:
        duration_seconds: Time taken to complete the scan
        operations_count: Total operations fetched
    """
    if not _metrics_enabled():
        return

    try:
        track_duration("operations_scan_duration_seconds", duration_seconds)
        track_gauge("operations_scan_count", operations_count)
        track_event("operations_scan_event")
        logger.info(
            f"Tracked operations scan: {duration_seconds:.2f}s, {operations_count} operations"
        )
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

    Query in App Insights:
        customMetrics
        | where name == "worker_operation_result"
        | extend operation = tostring(customDimensions.operation)
        | extend result = tostring(customDimensions.result)
        | summarize count() by operation, result, bin(timestamp, 1h)
    """
    props = {"operation": operation_name, "result": result}
    if error_message:
        # Truncate error message to avoid huge dimensions
        props["error"] = error_message[:200]

    parts = [f"Worker op={operation_name} result={result}"]
    if duration_seconds:
        parts.append(f"duration={duration_seconds:.2f}s")
    if error_message:
        parts.append(f"error={error_message[:100]}")
    logger.info(", ".join(parts))

    if not _metrics_enabled():
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
        duration_seconds: Time taken to execute the query
        rows_affected: Optional number of rows returned/affected

    Query in App Insights:
        customMetrics
        | where name == "db_query_duration_seconds"
        | extend query = tostring(customDimensions.query)
        | summarize avg(value), max(value) by query, bin(timestamp, 1h)
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

    Query in App Insights:
        customEvents
        | where name == "cache_access"
        | extend cache_type = tostring(customDimensions.cache_type)
        | extend hit = tobool(customDimensions.hit)
        | summarize hits=countif(hit), misses=countif(not(hit)) by cache_type, bin(timestamp, 1h)
        | extend hit_rate = round(100.0 * hits / (hits + misses), 2)
    """
    hit_str = "hit" if hit else "miss"
    key_str = f" key={key}" if key else ""
    logger.debug("Cache %s: %s%s", hit_str, cache_type, key_str)

    if not _metrics_enabled():
        return

    try:
        props: dict[str, Any] = {"cache_type": cache_type, "hit": hit}
        if key:
            props["key"] = key[:100]  # Truncate long keys
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

    Query in App Insights:
        customEvents
        | where name == "cache_refresh_failure"
        | extend source = tostring(customDimensions.source)
        | extend reason = tostring(customDimensions.reason)
        | summarize count() by source, reason, bin(timestamp, 1h)
    """
    logger.warning("Cache refresh failed (%s): %s", source, reason)

    if not _metrics_enabled():
        return

    try:
        track_event("cache_refresh_failure", {"source": source, "reason": reason})
    except Exception as e:
        logger.exception("Failed to track cache refresh failure: %s", e)


class TimedDbQuery(BaseTimer):
    """Context manager for timing database queries and tracking metrics.

    Usage:
        async with TimedDbQuery("fetch_roles") as timer:
            result = await session.execute(select(Role))
            rows = result.scalars().all()
            timer.rows = len(rows)

    Or simpler (without row count):
        with TimedDbQuery("fetch_roles"):
            result = await session.execute(select(Role))
    """

    __slots__ = ("query_name", "rows")

    def __init__(self, query_name: str) -> None:
        super().__init__()
        self.query_name = query_name
        self.rows: int | None = None

    def _on_exit(self, elapsed: float, exc_type: type[BaseException] | None) -> None:
        track_db_query(self.query_name, elapsed, self.rows)


class TimedOperation(BaseTimer):
    """Context manager for timing any operation with debug logging.

    Usage:
        with TimedOperation("computing role coverage", logger):
            # expensive computation
            pass

    Logs at DEBUG level when entering and exiting, with elapsed time.
    """

    __slots__ = ("log", "operation_name")

    def __init__(self, operation_name: str, log: logging.Logger | None = None) -> None:
        super().__init__()
        self.operation_name = operation_name
        self.log = log or logger

    def __enter__(self) -> Self:
        self.log.debug("Starting: %s", self.operation_name)
        return super().__enter__()

    async def __aenter__(self) -> Self:
        self.log.debug("Starting: %s", self.operation_name)
        return await super().__aenter__()

    def _on_exit(self, elapsed: float, exc_type: type[BaseException] | None) -> None:
        status = "Failed" if exc_type else "Completed"
        self.log.debug("%s: %s (%.3fs)", status, self.operation_name, elapsed)
