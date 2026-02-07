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
            ("track_gauge", ("test_metric", 42.0, {"dim": "value"})),
            ("track_startup", (5.0, 100, 5000)),
            ("track_cache_refresh", (3.0, "startup", 100, 5000)),
            ("track_role_scan", (100, 5, 3, 2)),
            ("track_operations_scan", (5000,)),
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

    def test_track_db_fallback_logs_correctly(self, local_env):
        """track_db_fallback should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_db_fallback("role_detail", "cache_miss", "test-role-id")
        metrics_module.track_db_fallback("role_history", "not_in_cache")

    def test_track_ai_recommendation_logs_correctly(self, local_env):
        """track_ai_recommendation should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_ai_recommendation("tfidf", 5)
        metrics_module.track_ai_recommendation("semantic", 0, is_error=True)
        metrics_module.track_ai_recommendation("colbert", 10)

    def test_track_role_recommendation_logs_correctly(self, local_env):
        """track_role_recommendation should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_role_recommendation(3, 10, 5)
        metrics_module.track_role_recommendation(1, 1, 0, duration_seconds=0.5)

    def test_track_cache_call_logs_correctly(self, local_env):
        """track_cache_call should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_cache_call("get_role_by_id")
        metrics_module.track_cache_call("search_operations")

    def test_track_db_query_logs_correctly(self, local_env):
        """track_db_query should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_db_query("fetch_roles", 0.05, rows_affected=100)
        metrics_module.track_db_query("count_operations", 0.01)

    def test_track_cache_hit_logs_correctly(self, local_env):
        """track_cache_hit should log and handle parameters."""
        metrics_module = local_env
        # Should not raise (no-op when local)
        metrics_module.track_cache_hit("role", True, "test-role-id")
        metrics_module.track_cache_hit("operation", False)


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
    def test_track_gauge_with_properties(self, local_env, properties):
        """track_gauge should accept various property types."""
        metrics_module = local_env
        metrics_module.track_gauge("test", 1.0, properties)

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

    def test_track_gauge_with_negative_value(self, local_env):
        """track_gauge should accept negative values."""
        metrics_module = local_env
        metrics_module.track_gauge("test", -42.0)


# =============================================================================
# Scan Tracking Functions (No Duration Parameter)
# =============================================================================


class TestTrackRoleScan:
    """Test track_role_scan function accepts correct parameters without duration."""

    @pytest.mark.parametrize(
        "roles_fetched,roles_added,roles_updated,roles_deleted",
        [
            pytest.param(0, 0, 0, 0, id="all_zeros"),
            pytest.param(100, 5, 3, 2, id="typical_scan"),
            pytest.param(500, 0, 0, 0, id="no_changes"),
            pytest.param(1, 1, 0, 0, id="single_add"),
            pytest.param(10, 0, 5, 0, id="only_updates"),
            pytest.param(10, 0, 0, 3, id="only_deletions"),
            pytest.param(1000, 50, 100, 25, id="large_scan"),
        ],
    )
    def test_track_role_scan_accepts_all_parameter_combinations(
        self,
        local_env,
        roles_fetched: int,
        roles_added: int,
        roles_updated: int,
        roles_deleted: int,
    ):
        """track_role_scan should accept various parameter combinations without duration."""
        metrics_module = local_env
        # Should not raise - no-op when local
        metrics_module.track_role_scan(roles_fetched, roles_added, roles_updated, roles_deleted)


class TestTrackOperationsScan:
    """Test track_operations_scan function accepts correct parameters without duration."""

    @pytest.mark.parametrize(
        "operations_count",
        [
            pytest.param(0, id="zero_operations"),
            pytest.param(1, id="single_operation"),
            pytest.param(5000, id="typical_count"),
            pytest.param(20000, id="large_count"),
        ],
    )
    def test_track_operations_scan_accepts_all_parameter_values(
        self, local_env, operations_count: int
    ):
        """track_operations_scan should accept various counts without duration."""
        metrics_module = local_env
        # Should not raise - no-op when local
        metrics_module.track_operations_scan(operations_count)


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


# =============================================================================
# Timer Tests
# =============================================================================


class TestTimedDbQuery:
    """Tests for TimedDbQuery context manager."""

    def test_sync_tracks_query_with_metrics(self):
        """Test that sync context manager tracks query metrics."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with patch("azurerbac.telemetry.timers.track_db_query") as mock_track:
            with TimedDbQuery("test_query") as timer:
                timer.rows = 10

            mock_track.assert_called_once()
            args = mock_track.call_args[0]
            assert args[0] == "test_query"
            assert isinstance(args[1], float)  # elapsed time
            assert args[2] == 10  # rows

    def test_sync_tracks_query_without_row_count(self):
        """Test that query is tracked even when row count not set."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with patch("azurerbac.telemetry.timers.track_db_query") as mock_track:
            with TimedDbQuery("no_rows_query"):
                pass

            mock_track.assert_called_once()
            args = mock_track.call_args[0]
            assert args[0] == "no_rows_query"
            assert args[2] is None  # rows not set

    @pytest.mark.asyncio
    async def test_async_tracks_query_with_metrics(self):
        """Test that async context manager tracks query metrics."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with patch("azurerbac.telemetry.timers.track_db_query") as mock_track:
            async with TimedDbQuery("async_test_query") as timer:
                timer.rows = 25

            mock_track.assert_called_once()
            args = mock_track.call_args[0]
            assert args[0] == "async_test_query"
            assert isinstance(args[1], float)
            assert args[2] == 25

    @pytest.mark.parametrize(
        ("query_name", "row_count"),
        [
            pytest.param("fetch_roles", 100, id="fetch_roles"),
            pytest.param("count_operations", 5000, id="count_operations"),
            pytest.param("update_history", 1, id="single_row_update"),
            pytest.param("bulk_insert", 0, id="zero_rows"),
        ],
    )
    def test_various_query_types(self, query_name: str, row_count: int):
        """Test timer works with various query names and row counts."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with patch("azurerbac.telemetry.timers.track_db_query") as mock_track:
            with TimedDbQuery(query_name) as timer:
                timer.rows = row_count

            args = mock_track.call_args[0]
            assert args[0] == query_name
            assert args[2] == row_count

    def test_sync_tracks_error_on_exception(self):
        """Test that sync context manager tracks error when exception occurs."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with (
            patch("azurerbac.telemetry.timers.track_db_query") as mock_success,
            patch("azurerbac.telemetry.timers.track_db_query_error") as mock_error,
            patch("azurerbac.telemetry.timers.track_db_fallback") as mock_fallback,
            pytest.raises(ValueError),
            TimedDbQuery("failing_query", fallback_type="test_fallback"),
        ):
            raise ValueError("Test error")

        # Error tracking should be called
        mock_error.assert_called_once()
        args = mock_error.call_args[0]
        assert args[0] == "failing_query"
        assert isinstance(args[1], float)  # elapsed time
        assert args[2] == "ValueError"  # exception type name

        # Success tracking should NOT be called (early return)
        mock_success.assert_not_called()
        mock_fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_tracks_error_on_exception(self):
        """Test that async context manager tracks error when exception occurs."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with (
            patch("azurerbac.telemetry.timers.track_db_query") as mock_success,
            patch("azurerbac.telemetry.timers.track_db_query_error") as mock_error,
            patch("azurerbac.telemetry.timers.track_db_fallback") as mock_fallback,
            pytest.raises(RuntimeError),
        ):
            async with TimedDbQuery("async_failing_query", fallback_type="test_fallback"):
                raise RuntimeError("Async test error")

        # Error tracking should be called
        mock_error.assert_called_once()
        args = mock_error.call_args[0]
        assert args[0] == "async_failing_query"
        assert isinstance(args[1], float)
        assert args[2] == "RuntimeError"

        # Success tracking should NOT be called
        mock_success.assert_not_called()
        mock_fallback.assert_not_called()

    def test_fallback_type_tracks_cache_miss_on_success(self):
        """Test that fallback_type triggers track_db_fallback on successful query."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with (
            patch("azurerbac.telemetry.timers.track_db_query") as mock_query,
            patch("azurerbac.telemetry.timers.track_db_fallback") as mock_fallback,
        ):
            with TimedDbQuery("test_query", fallback_type="dashboard_roles") as timer:
                timer.rows = 5

            # Both query and fallback should be tracked
            mock_query.assert_called_once()
            mock_fallback.assert_called_once_with("dashboard_roles", "cache_miss", "test_query")

    def test_fallback_type_none_does_not_track_fallback(self):
        """Test that fallback_type=None does not trigger track_db_fallback."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with (
            patch("azurerbac.telemetry.timers.track_db_query") as mock_query,
            patch("azurerbac.telemetry.timers.track_db_fallback") as mock_fallback,
        ):
            with TimedDbQuery("test_query"):
                pass

            mock_query.assert_called_once()
            mock_fallback.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_fallback_type_tracks_cache_miss_on_success(self):
        """Test async fallback_type triggers track_db_fallback on successful query."""
        from unittest.mock import patch

        from azurerbac.telemetry.timers import TimedDbQuery

        with (
            patch("azurerbac.telemetry.timers.track_db_query") as mock_query,
            patch("azurerbac.telemetry.timers.track_db_fallback") as mock_fallback,
        ):
            async with TimedDbQuery("async_test", fallback_type="search_roles") as timer:
                timer.rows = 10

            mock_query.assert_called_once()
            mock_fallback.assert_called_once_with("search_roles", "cache_miss", "async_test")


class TestTrackDbQueryError:
    """Tests for track_db_query_error function."""

    def test_logs_warning_with_correct_format(self, caplog):
        """Test that error is logged at WARNING level with expected format."""
        import logging

        from azurerbac.telemetry.metrics import track_db_query_error

        with caplog.at_level(logging.WARNING, logger="azurerbac.telemetry.metrics"):
            track_db_query_error("test_query", 1.234, "ValueError")

        assert "DB query failed: test_query (1.234s) error=ValueError" in caplog.text
        assert caplog.records[0].levelno == logging.WARNING

    def test_tracks_metrics_when_enabled(self, local_env):
        """Test proper metric tracking when metrics are enabled."""
        metrics_module = local_env

        with (
            patch.object(metrics_module, "_metrics_enabled", return_value=True),
            patch.object(metrics_module, "track_duration") as mock_duration,
            patch.object(metrics_module, "track_event") as mock_event,
        ):
            metrics_module.track_db_query_error("failed_query", 2.5, "RuntimeError")

            mock_duration.assert_called_once_with(
                "db_query_error_duration_seconds",
                2.5,
                {"query": "failed_query", "error_type": "RuntimeError"},
            )
            mock_event.assert_called_once_with(
                "db_query_error_event",
                {"query": "failed_query", "error_type": "RuntimeError"},
            )

    def test_does_not_track_metrics_when_disabled(self, local_env, caplog):
        """Test behavior when metrics are disabled (local env)."""
        import logging

        metrics_module = local_env

        with (
            patch.object(metrics_module, "track_duration") as mock_duration,
            patch.object(metrics_module, "track_event") as mock_event,
        ):
            with caplog.at_level(logging.WARNING, logger="azurerbac.telemetry.metrics"):
                metrics_module.track_db_query_error("query", 1.0, "Error")

            # Logging still happens
            assert "DB query failed" in caplog.text
            # But metrics are not tracked
            mock_duration.assert_not_called()
            mock_event.assert_not_called()

    def test_handles_metric_tracking_failure_gracefully(self, local_env, caplog):
        """Test exception handling when metric tracking fails."""
        import logging

        metrics_module = local_env

        with (
            patch.object(metrics_module, "_metrics_enabled", return_value=True),
            patch.object(
                metrics_module, "track_duration", side_effect=Exception("Tracking failed")
            ),
            caplog.at_level(logging.WARNING, logger="azurerbac.telemetry.metrics"),
        ):
            # Should not raise
            metrics_module.track_db_query_error("query", 1.0, "Error")

            assert "Failed to track db query error" in caplog.text


# =============================================================================
# Tracing Context Tests
# =============================================================================


class TestWorkerOperationContext:
    """Tests for WorkerOperationContext tracing context manager."""

    @pytest.mark.parametrize(
        ("operation_name",),
        [
            pytest.param("role-scan", id="role_scan"),
            pytest.param("operations-sync", id="operations_sync"),
            pytest.param("cache-refresh", id="cache_refresh"),
        ],
    )
    def test_context_manager_enters_and_exits(self, operation_name: str):
        """Test context manager enters and exits without error."""
        from azurerbac.telemetry.tracing import WorkerOperationContext

        with WorkerOperationContext(operation_name) as ctx:
            assert ctx.operation_name == operation_name

    def test_does_not_suppress_exceptions(self):
        """Test exceptions are not suppressed by context manager."""
        from azurerbac.telemetry.tracing import WorkerOperationContext

        with (
            pytest.raises(ValueError, match="test error"),
            WorkerOperationContext("failing-operation"),
        ):
            raise ValueError("test error")

    def test_handles_missing_opentelemetry_gracefully(self):
        """Test graceful handling when opentelemetry is not available."""
        from unittest.mock import patch

        from azurerbac.telemetry.tracing import WorkerOperationContext

        with (
            patch.dict("sys.modules", {"opentelemetry": None}),
            patch(
                "azurerbac.telemetry.tracing.WorkerOperationContext.__enter__",
                side_effect=lambda self: self,
            ),
        ):
            # Should not raise when opentelemetry is unavailable
            ctx = WorkerOperationContext("test-op")
            assert ctx.operation_name == "test-op"
