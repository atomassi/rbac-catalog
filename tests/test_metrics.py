"""Tests for the metrics module."""

import importlib
import os
from unittest.mock import MagicMock, patch

import pytest

from azurerbac.cache.models import CachedChangeEvent
from azurerbac.core.constants import EventType

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def local_env():
    """Set up local environment (no App Insights, no WEBSITE_SITE_NAME)."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WEBSITE_SITE_NAME", None)
        os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

        import azurerbac.telemetry.metrics as metrics_module

        importlib.reload(metrics_module)

        yield metrics_module


@pytest.fixture
def azure_env():
    """Set up Azure environment with WEBSITE_SITE_NAME but no connection string."""
    with patch.dict(os.environ, {"WEBSITE_SITE_NAME": "test-app"}, clear=False):
        os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

        import azurerbac.telemetry.metrics as metrics_module

        importlib.reload(metrics_module)

        yield metrics_module


@pytest.fixture
def mock_app_cache():
    """Create a mock app_cache with data property."""
    mock_data = MagicMock()
    mock_data.roles_by_id = {"role1": {}, "role2": {}}
    mock_data.all_operations = [{"name": "op1"}, {"name": "op2"}, {"name": "op3"}]
    mock_data.all_change_events = [
        CachedChangeEvent(id=1, role_id="r1", role_name="Role 1", event_type=EventType.CREATED)
    ]

    mock_cache = MagicMock()
    mock_cache.data = mock_data
    mock_cache._role_pages = {"key1": "val1"}
    return mock_cache


@pytest.fixture
def reset_metrics_sender():
    """Reset MetricsSender state between tests."""
    from azurerbac.telemetry.sender import MetricsSender

    original_initialized = MetricsSender._initialized
    original_meter = MetricsSender._meter
    original_histograms = MetricsSender._histograms.copy()
    original_gauges = MetricsSender._gauges.copy()
    original_counters = MetricsSender._counters.copy()

    yield

    MetricsSender._initialized = original_initialized
    MetricsSender._meter = original_meter
    MetricsSender._histograms = original_histograms
    MetricsSender._gauges = original_gauges
    MetricsSender._counters = original_counters


# =============================================================================
# MetricsSender Unit Tests
# =============================================================================


class TestMetricsSenderUnit:
    """Unit tests for MetricsSender class."""

    def test_send_gauge_noop_when_not_initialized(self, local_env, reset_metrics_sender):
        """send_gauge should be a no-op when not in Azure."""
        from azurerbac.telemetry.sender import MetricsSender

        MetricsSender._initialized = False
        MetricsSender._meter = None

        # Should not raise
        MetricsSender.send_gauge("test_gauge", 42.0, {"key": "value"})

    def test_send_histogram_noop_when_not_initialized(self, local_env, reset_metrics_sender):
        """send_histogram should be a no-op when not in Azure."""
        from azurerbac.telemetry.sender import MetricsSender

        MetricsSender._initialized = False
        MetricsSender._meter = None

        # Should not raise
        MetricsSender.send_histogram("test_hist", 1.5, {"key": "value"})

    def test_send_count_noop_when_not_initialized(self, local_env, reset_metrics_sender):
        """send_count should be a no-op when not in Azure."""
        from azurerbac.telemetry.sender import MetricsSender

        MetricsSender._initialized = False
        MetricsSender._meter = None

        # Should not raise
        MetricsSender.send_count("test_count", 5.0)

    def test_flush_returns_false_when_not_initialized(self, local_env, reset_metrics_sender):
        """flush should return False when not initialized."""
        from azurerbac.telemetry.sender import MetricsSender

        MetricsSender._initialized = False
        MetricsSender._meter = None

        result = MetricsSender.flush()
        assert result is False

    def test_get_histogram_creates_histogram(self, reset_metrics_sender):
        """_get_histogram should create and cache histograms."""
        from azurerbac.telemetry.sender import MetricsSender

        mock_histogram = MagicMock()
        mock_meter = MagicMock()
        mock_meter.create_histogram.return_value = mock_histogram

        MetricsSender._meter = mock_meter
        MetricsSender._histograms = {}

        result = MetricsSender._get_histogram("test", "description")
        assert result is mock_histogram
        mock_meter.create_histogram.assert_called_once()

        # Second call should reuse cached
        result2 = MetricsSender._get_histogram("test", "description")
        assert result2 is mock_histogram
        assert mock_meter.create_histogram.call_count == 1


# =============================================================================
# Local Mode Tests - Metrics Functions Are No-Op
# =============================================================================


class TestMetricsLocalMode:
    """Test that metrics are disabled when running locally."""

    @pytest.mark.parametrize(
        "func_name,args",
        [
            ("track_metric", ("test_metric", 42.0, {"dim": "value"})),
            ("track_startup", (5.0, 100, 5000)),
            ("track_cache_refresh", (3.0, "startup", 100, 5000)),
            ("track_role_scan", (10.0, 100, 5, 3, 2)),
            ("track_operations_scan", (30.0, 5000)),
        ],
    )
    def test_metrics_noop_when_local(self, local_env, func_name, args):
        """All tracking functions should be no-op when IS_LOCAL is True."""
        metrics_module = local_env
        func = getattr(metrics_module, func_name)
        # Should not raise, just no-op
        func(*args)


class TestMetricsSenderSingleton:
    """Test the MetricsSender singleton pattern."""

    def test_initialize_returns_false_when_local(self, local_env):
        """MetricsSender.initialize() should return False when running locally."""
        metrics_module = local_env
        result = metrics_module.MetricsSender.initialize()
        assert result is False

    def test_initialize_returns_false_when_no_connection_string(self, azure_env):
        """MetricsSender.initialize() needs connection string even in Azure."""
        metrics_module = azure_env
        # Even with WEBSITE_SITE_NAME set, no connection string = no metrics
        result = metrics_module.MetricsSender.initialize()
        assert result is False


# =============================================================================
# Track Functions Accept Correct Parameters
# =============================================================================


class TestTrackFunctions:
    """Test individual track functions accept correct parameters."""

    def test_track_cache_stats_extracts_correct_values(self, local_env, mock_app_cache):
        """track_cache_stats should extract values from cache_container."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_cache_stats(mock_app_cache)


# =============================================================================
# Dimensions and Properties
# =============================================================================


class TestMetricsDimensions:
    """Test that dimensions/properties are handled correctly."""

    @pytest.mark.parametrize(
        "properties",
        [
            {"str_dim": "value"},
            {"int_dim": "123"},
            {"multi": "a", "dims": "b"},
            None,
            {},
        ],
    )
    def test_track_metric_with_properties(self, local_env, properties):
        """track_metric should accept various property types."""
        metrics_module = local_env
        metrics_module.track_metric("test", 1.0, properties)

    @pytest.mark.parametrize("source", ["startup", "worker", "periodic", "manual"])
    def test_track_cache_refresh_with_source_dimension(self, local_env, source):
        """track_cache_refresh should use source as dimension."""
        metrics_module = local_env
        metrics_module.track_cache_refresh(1.0, source, 100, 5000)


# =============================================================================
# Error Handling
# =============================================================================


class TestMetricsErrorHandling:
    """Test that metrics functions handle errors gracefully."""

    def test_track_cache_stats_handles_missing_attributes(self, local_env):
        """track_cache_stats should handle missing cache attributes."""
        metrics_module = local_env
        mock_cache = MagicMock(spec=[])  # Empty spec = no attributes
        # Should not raise - error is caught
        metrics_module.track_cache_stats(mock_cache)

    def test_track_role_scan_with_zero_values(self, local_env):
        """track_role_scan should handle zero values."""
        metrics_module = local_env
        metrics_module.track_role_scan(0.0, 0, 0, 0, 0)

    def test_track_metric_with_negative_value(self, local_env):
        """track_metric should accept negative values."""
        metrics_module = local_env
        metrics_module.track_metric("test", -42.0)


# =============================================================================
# Module Constants (Removed)
# =============================================================================
# Note: IS_LOCAL and APP_INSIGHTS_CONNECTION_STRING were removed from config.py
# Consumers now use is_running_in_azure() and get_settings() directly from core.
# These functions are tested in test_core.py


# =============================================================================
# OpenTelemetry Integration
# =============================================================================


class TestOpenTelemetryIntegration:
    """Test OpenTelemetry-specific functionality."""

    @pytest.mark.parametrize(
        "func_name,args",
        [
            ("track_duration", ("test_duration", 1.5, {"test": "value"})),
            ("track_event", ("test_event", {"test": "value"})),
            ("track_gauge", ("test_gauge", 42.0, {"test": "value"})),
        ],
    )
    def test_track_functions_exist_and_callable(self, func_name, args):
        """Test various track functions exist and are callable."""
        from azurerbac.telemetry import metrics

        func = getattr(metrics, func_name)
        assert callable(func)
        # Should not raise when called locally
        func(*args)

    def test_flush_metrics_exists_and_callable(self):
        """Test flush_metrics function exists and is callable."""
        from azurerbac.telemetry.metrics import flush_metrics

        assert callable(flush_metrics)

    def test_flush_metrics_returns_false_when_not_initialized(self, local_env):
        """Test flush_metrics returns False when not initialized."""
        metrics_module = local_env
        # Reset initialization state
        metrics_module.MetricsSender._initialized = False
        metrics_module.MetricsSender._meter = None

        result = metrics_module.flush_metrics()
        assert result is False

    def test_flush_metrics_exported_from_init(self):
        """Test flush_metrics is exported from telemetry module."""
        from azurerbac.telemetry import flush_metrics

        assert callable(flush_metrics)
