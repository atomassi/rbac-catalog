"""Telemetry: Application Insights metrics and logging."""

from .dimensions import CacheType, DbFallbackType, DbQueryName
from .logging import configure_logging, sanitize_for_log
from .metric_names import MetricName
from .metrics import (
    flush_metrics,
    track_ai_recommendation,
    track_ai_recommendation_error,
    track_cache_call,
    track_cache_hit,
    track_cache_refresh,
    track_cache_refresh_failure,
    track_duration,
    track_event,
    track_gauge,
    track_operations_scan,
    track_role_recommendation,
    track_role_scan,
    track_startup,
    track_worker_result,
)
from .timers import TimedDbQuery
from .tracing import WorkerOperationContext

__all__ = [
    "CacheType",
    "DbFallbackType",
    "DbQueryName",
    "MetricName",
    "TimedDbQuery",
    "WorkerOperationContext",
    "configure_logging",
    "flush_metrics",
    "sanitize_for_log",
    "track_ai_recommendation",
    "track_ai_recommendation_error",
    "track_cache_call",
    "track_cache_hit",
    "track_cache_refresh",
    "track_cache_refresh_failure",
    "track_duration",
    "track_event",
    "track_gauge",
    "track_operations_scan",
    "track_role_recommendation",
    "track_role_scan",
    "track_startup",
    "track_worker_result",
]
