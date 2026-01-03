"""Telemetry module: Application Insights metrics and logging."""

from .logging import WorkerOperationContext, configure_logging, get_logger
from .metrics import (
    TimedDbQuery,
    TimedOperation,
    flush_metrics,
    track_cache_hit,
    track_cache_refresh,
    track_cache_refresh_failure,
    track_cache_stats,
    track_db_query,
    track_duration,
    track_event,
    track_gauge,
    track_metric,
    track_operations_scan,
    track_role_scan,
    track_startup,
    track_worker_result,
)

__all__ = [
    "TimedDbQuery",
    "TimedOperation",
    "WorkerOperationContext",
    "configure_logging",
    "flush_metrics",
    "get_logger",
    "track_cache_hit",
    "track_cache_refresh",
    "track_cache_refresh_failure",
    "track_cache_stats",
    "track_db_query",
    "track_duration",
    "track_event",
    "track_gauge",
    "track_metric",
    "track_operations_scan",
    "track_role_scan",
    "track_startup",
    "track_worker_result",
]
