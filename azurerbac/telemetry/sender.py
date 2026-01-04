"""Metrics sender singleton for OpenTelemetry integration.

This module provides the core MetricsSender class that handles
lazy initialization and sending of metrics to Azure Application Insights
via OpenTelemetry.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from azurerbac.settings import Settings, is_running_in_azure

logger = logging.getLogger(__name__)


class MetricsSender:
    """Lazy-loaded singleton for sending custom metrics via OpenTelemetry."""

    _meter: ClassVar[Any] = None
    _histograms: ClassVar[dict[str, Any]] = {}
    _gauges: ClassVar[dict[str, Any]] = {}
    _counters: ClassVar[dict[str, Any]] = {}
    _initialized: ClassVar[bool] = False

    @classmethod
    def initialize(cls) -> bool:
        """Initialize the OpenTelemetry meter (lazy initialization).

        NOTE: configure_logging() must be called first to set up Azure Monitor.
        This is handled automatically by app.py and worker.py at startup.
        """
        settings = Settings.get()
        if not is_running_in_azure():
            logger.debug("Metrics disabled: not running in Azure")
            return False
        if not settings.app_insights_connection_string:
            logger.debug("Metrics disabled: no App Insights connection string")
            return False

        if cls._initialized:
            return cls._meter is not None

        cls._initialized = True
        try:
            from opentelemetry import metrics

            cls._meter = metrics.get_meter("azurerbac.telemetry")
            logger.info("OpenTelemetry metrics initialized")
            return True
        except ImportError:
            logger.warning("opentelemetry not installed, metrics disabled")
        except Exception as e:
            logger.exception("Failed to initialize OpenTelemetry metrics: %s", e)
        return False

    @classmethod
    def _get_histogram(cls, name: str, description: str, unit: str = "s") -> Any:
        """Get or create a histogram for the given name."""
        if name not in cls._histograms:
            cls._histograms[name] = cls._meter.create_histogram(
                name=name,
                description=description,
                unit=unit,
            )
        return cls._histograms[name]

    @classmethod
    def send_gauge(cls, name: str, value: float, properties: dict[str, Any] | None = None) -> None:
        """Send a gauge metric (current value) to Application Insights.

        Use for: counts, sizes, current state values.
        """
        if not cls.initialize():
            return

        try:
            attributes = {k: str(v) for k, v in (properties or {}).items()}

            # Use Gauge for current value reporting
            if name not in cls._gauges:
                cls._gauges[name] = cls._meter.create_gauge(
                    name=name,
                    description=f"Gauge: {name}",
                )
            cls._gauges[name].set(value, attributes)

        except Exception as e:
            logger.exception("Failed to send gauge %s: %s", name, e)

    @classmethod
    def send_histogram(
        cls, name: str, value: float, properties: dict[str, Any] | None = None
    ) -> None:
        """Send a histogram metric (distribution) to Application Insights.

        Use for: durations, latencies where you want min/max/avg/percentiles.
        """
        if not cls.initialize():
            return

        try:
            attributes = {k: str(v) for k, v in (properties or {}).items()}
            histogram = cls._get_histogram(name, f"Histogram: {name}")
            histogram.record(value, attributes)
        except Exception as e:
            logger.exception("Failed to send histogram %s: %s", name, e)

    @classmethod
    def send_count(
        cls, name: str, value: float = 1, properties: dict[str, Any] | None = None
    ) -> None:
        """Send a count metric (increment counter) to Application Insights.

        Use for: event counts, occurrences.
        """
        if not cls.initialize():
            return

        try:
            attributes = {k: str(v) for k, v in (properties or {}).items()}

            if name not in cls._counters:
                cls._counters[name] = cls._meter.create_counter(
                    name=name,
                    description=f"Counter: {name}",
                )
            cls._counters[name].add(int(value), attributes)
        except Exception as e:
            logger.exception("Failed to send count %s: %s", name, e)

    @classmethod
    def flush(cls, timeout_ms: int | None = None) -> bool:
        """Force flush all pending metrics to Azure Monitor.

        Call this before process exit or when you need metrics exported immediately.
        For long-running web apps, this is typically not needed as metrics are
        exported periodically (default: every 60 seconds).

        Args:
            timeout_ms: Timeout in milliseconds for the flush operation.
                       Defaults to settings.telemetry_flush_timeout_ms.

        Returns:
            True if flush succeeded, False otherwise.
        """
        if not cls._initialized or cls._meter is None:
            return False

        try:
            from opentelemetry import metrics

            if timeout_ms is None:
                timeout_ms = Settings.get().telemetry_flush_timeout_ms

            provider = metrics.get_meter_provider()
            if hasattr(provider, "force_flush"):
                return provider.force_flush(timeout_millis=timeout_ms)  # type: ignore[union-attr]
        except Exception as e:
            logger.exception("Failed to flush metrics: %s", e)
        return False
