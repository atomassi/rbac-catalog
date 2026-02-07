"""Telemetry: Application Insights metrics and logging."""

from .logging import configure_logging
from .metrics import (
    flush_metrics,
    track_ai_recommendation,
    track_cache_call,
    track_cache_hit,
    track_cache_refresh,
    track_cache_refresh_failure,
    track_db_query,
    track_duration,
    track_event,
    track_gauge,
    track_operations_scan,
    track_role_recommendation,
    track_role_scan,
    track_startup,
    track_worker_result,
)
from .timers import TimedDbQuery, TimedOperation
from .tracing import WorkerOperationContext

__all__ = [
    "TimedDbQuery",
    "TimedOperation",
    "WorkerOperationContext",
    "configure_logging",
    "flush_metrics",
    "track_ai_recommendation",
    "track_cache_call",
    "track_cache_hit",
    "track_cache_refresh",
    "track_cache_refresh_failure",
    "track_db_query",
    "track_duration",
    "track_event",
    "track_gauge",
    "track_operations_scan",
    "track_role_recommendation",
    "track_role_scan",
    "track_startup",
    "track_worker_result",
]
