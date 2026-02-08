"""Comprehensive tests for the core modules: config, db, and logging_setup."""

from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from azurerbac.settings import Settings


class TestSettings:
    """Tests for Settings model."""

    def test_custom_values(self):
        """Test Settings accepts custom values."""
        settings = Settings(
            roles_poll_interval_seconds=300,
            db_connection_string="postgresql+asyncpg://user:pass@host/db",
        )
        assert settings.roles_poll_interval_seconds == 300
        assert settings.db_connection_string == "postgresql+asyncpg://user:pass@host/db"

    def test_partial_override(self):
        """Test Settings allows partial override of defaults."""
        settings = Settings(roles_poll_interval_seconds=120)
        assert settings.roles_poll_interval_seconds == 120
        assert settings.db_connection_string == "sqlite+aiosqlite:///./azurerbac.db"

    def test_invalid_poll_interval_type(self):
        """Test Settings rejects invalid types."""
        with pytest.raises(ValidationError):
            Settings(roles_poll_interval_seconds="not-a-number")


class TestGetSettings:
    """Tests for get_settings function."""

    def test_get_settings_reads_env_vars(self):
        """Test get_settings reads values from environment variables."""
        with patch.dict(
            os.environ,
            {
                "ROLES_POLL_INTERVAL_SECONDS": "120",
                "DB_CONNECTION_STRING": "postgresql+asyncpg://env@host/db",
            },
            clear=True,
        ):
            settings = Settings.get()
            assert settings.roles_poll_interval_seconds == 120
            assert settings.db_connection_string == "postgresql+asyncpg://env@host/db"

    def test_get_settings_uses_defaults_when_env_not_set(self):
        """Test get_settings uses model defaults when env vars are not set."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.get()
            # Non-provided values use defaults from Settings model
            assert settings.roles_poll_interval_seconds > 0  # Has a valid default
            assert settings.db_connection_string.startswith("sqlite")  # Default is SQLite
            assert settings.role_scan_enabled is True  # Default
            assert settings.mcp_server_enabled is True  # Default

    def test_get_settings_boolean_parsing(self):
        """Test get_settings correctly parses boolean env vars."""
        with patch.dict(
            os.environ,
            {
                "ROLE_SCAN_ENABLED": "false",
                "OPERATIONS_SCAN_ENABLED": "0",
                "RUN_SCAN_ON_STARTUP": "yes",
                "MCP_SERVER_ENABLED": "true",
            },
            clear=True,
        ):
            settings = Settings.get()
            assert settings.role_scan_enabled is False
            assert settings.operations_scan_enabled is False
            assert settings.run_roles_scan_on_startup is True
            assert settings.mcp_server_enabled is True


class TestDatabaseEngine:
    """Tests for database engine and session creation."""

    @pytest.mark.asyncio
    async def test_engine_and_session_work_correctly(self):
        """Test creating SQLite async engine and session produces working DB access."""
        from sqlalchemy import text

        from azurerbac.core.db import EngineFactory, create_sessionmaker

        engine = EngineFactory.from_connection_string("sqlite+aiosqlite:///:memory:")
        assert "sqlite" in str(engine.url)

        session_factory = create_sessionmaker(engine)
        assert callable(session_factory)

        async with session_factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

        await engine.dispose()

    def test_from_connection_string_sqlite(self):
        """Test creating SQLite engine from connection string."""
        from azurerbac.core.db import EngineFactory

        engine = EngineFactory.from_connection_string("sqlite+aiosqlite:///:memory:")
        assert "sqlite" in str(engine.url)

    def test_from_connection_string_postgres(self):
        """Test PostgreSQL engine creation from connection string."""
        from azurerbac.core.db import EngineFactory

        engine = EngineFactory.from_connection_string(
            "postgresql+asyncpg://user:pass@localhost:5432/db",
        )
        assert "postgresql" in str(engine.url)
        # Check pool settings
        assert engine.pool.size() == 10

    def test_from_managed_identity(self):
        """Test PostgreSQL engine creation with managed identity uses async_creator."""
        from azurerbac.core.db import EngineFactory

        engine = EngineFactory.from_managed_identity(
            host="server.postgres.database.azure.com",
            database="db",
            user="myapp",
            port=5432,
        )
        assert "postgresql" in str(engine.url)
        # Pool should still be configured
        assert engine.pool.size() == 10


class TestManagedIdentityAuthenticator:
    """Tests for ManagedIdentityAuthenticator."""

    def test_credential_lazy_loaded(self):
        """Test credential is not loaded until first access."""
        from azurerbac.core.db import ManagedIdentityAuthenticator

        auth = ManagedIdentityAuthenticator()
        assert auth._credential is None

    @pytest.mark.asyncio
    async def test_get_token_calls_credential(self):
        """Test get_token fetches token from Azure credential."""
        from unittest.mock import AsyncMock, MagicMock

        from azurerbac.core.db import ManagedIdentityAuthenticator

        auth = ManagedIdentityAuthenticator()

        # Mock the credential
        mock_token = MagicMock()
        mock_token.token = "test-access-token-12345"

        mock_credential = AsyncMock()
        mock_credential.get_token = AsyncMock(return_value=mock_token)
        auth._credential = mock_credential

        token = await auth.get_token()

        assert token == "test-access-token-12345"
        mock_credential.get_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_cleans_up_credential(self):
        """Test _close properly cleans up the credential."""
        from unittest.mock import AsyncMock

        from azurerbac.core.db import ManagedIdentityAuthenticator

        auth = ManagedIdentityAuthenticator()

        mock_credential = AsyncMock()
        auth._credential = mock_credential

        await auth._close()

        mock_credential.close.assert_called_once()
        assert auth._credential is None

    @pytest.mark.asyncio
    async def test_close_handles_no_credential(self):
        """Test _close is safe when no credential exists."""
        from azurerbac.core.db import ManagedIdentityAuthenticator

        auth = ManagedIdentityAuthenticator()
        # Should not raise
        await auth._close()
        assert auth._credential is None


class TestLoggingSetup:
    """Tests for telemetry logging setup module."""

    def test_configure_creates_log_file_locally(self):
        """Test that configure_logging creates a log file when running locally."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the LOGS_DIR to use temp directory
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False  # Reset the configured flag

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)

                # Reset root logger handlers
                logging.getLogger().handlers = []

                logfile = logging_module.configure_logging("test_component")

                assert logfile is not None
                assert "test_component" in logfile
                # Check file exists
                assert Path(logfile).exists()

            # Restore
            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_log_filename_format(self):
        """Test log filename is {component}.log."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logfile = logging_module.configure_logging("worker")

                # Check format: {component}.log
                filename = Path(logfile).name
                assert filename == "worker.log"

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_respects_log_level_env(self):
        """Test that LOG_LEVEL environment variable is respected."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logging_module.configure_logging("test_level")

                root = logging.getLogger()
                assert root.level == logging.DEBUG

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_default_log_level_is_info(self):
        """Test default log level is INFO."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LOG_LEVEL", None)
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logging_module.configure_logging("test_default")

                root = logging.getLogger()
                assert root.level == logging.INFO

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_creates_logs_directory(self):
        """Test that logs directory is created if it doesn't exist."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir) / "nested" / "logs"
            assert not logs_dir.exists()

            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = logs_dir
            logging_module._configured = False

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logging_module.configure_logging("test_mkdir")

                assert logs_dir.exists()

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_no_file_handler_in_azure(self):
        """Test that no file handler is created in Azure App Service."""
        import importlib

        import azurerbac.telemetry.logging as logging_module

        # Reload logging to pick up environment changes
        # Clear APPLICATIONINSIGHTS_CONNECTION_STRING to avoid sending real telemetry
        with patch.dict(
            os.environ,
            {"APP_ENVIRONMENT_NAME": "production", "APPLICATIONINSIGHTS_CONNECTION_STRING": ""},
            clear=False,
        ):
            importlib.reload(logging_module)
            logging_module._configured = False
            logging.getLogger().handlers = []

            logging_module.configure_logging("azure_test")

            root = logging.getLogger()
            # Should have no FileHandler in deployed environments
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) == 0

        # Reload to restore local environment
        os.environ.pop("APP_ENVIRONMENT_NAME", None)
        importlib.reload(logging_module)

    def test_has_stream_handler_locally(self):
        """Test that StreamHandler is created locally."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logging_module.configure_logging("stream_test")

                root = logging.getLogger()
                stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
                assert len(stream_handlers) >= 1

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False


class TestCredentialFilter:
    """Tests for credential filtering in logs."""

    @pytest.mark.parametrize(
        ("message", "sensitive_text"),
        [
            pytest.param(
                "Request with Bearer token123abc",
                "token123abc",
                id="bearer_token",
            ),
            pytest.param(
                "Connection string with password=secret123",
                "secret123",
                id="password",
            ),
            pytest.param(
                "Using api_key=myapikey123",
                "myapikey123",
                id="api_key",
            ),
            pytest.param(
                "Has BEARER TOKEN123 uppercase",
                "TOKEN123",
                id="bearer_uppercase",
            ),
        ],
    )
    def test_credential_filter_redacts_sensitive_data(self, message, sensitive_text):
        """Test that sensitive data (tokens, passwords, API keys) are redacted from logs."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert "[REDACTED" in record.msg
        assert sensitive_text not in record.msg

    def test_credential_filter_passes_normal_messages(self):
        """Test that normal messages are not modified."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        original_msg = "Normal log message without sensitive data"
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=original_msg,
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert record.msg == original_msg


class TestLoggingEdgeCases:
    """Edge case tests for logging setup."""

    def test_invalid_log_level_falls_back_to_info(self):
        """Test that invalid LOG_LEVEL falls back to INFO."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {"LOG_LEVEL": "INVALID_LEVEL"}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                logging_module.configure_logging("invalid_level")

                root = logging.getLogger()
                # getattr with fallback should give INFO
                assert root.level == logging.INFO

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False

    def test_multiple_calls_only_configures_once(self):
        """Test that calling configure_logging multiple times only configures once."""
        import azurerbac.telemetry.logging as logging_module

        with tempfile.TemporaryDirectory() as tmpdir:
            original_logs_dir = logging_module.LOGS_DIR
            logging_module.LOGS_DIR = Path(tmpdir)
            logging_module._configured = False

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WEBSITE_SITE_NAME", None)
                logging.getLogger().handlers = []

                # First call configures
                result1 = logging_module.configure_logging("first")
                handler_count_1 = len(logging.getLogger().handlers)

                # Second call should be a no-op
                result2 = logging_module.configure_logging("second")

                # Should return the already-configured log file path
                assert result2 == result1
                # Handler count should be the same
                assert len(logging.getLogger().handlers) == handler_count_1

            logging_module.LOGS_DIR = original_logs_dir
            logging_module._configured = False


class TestConfigIntegration:
    """Integration tests combining config components."""

    def test_settings_used_in_engine_creation(self):
        """Test that settings can be used to create database engine."""
        from azurerbac.core.db import EngineFactory
        from azurerbac.settings import Settings

        settings = Settings(db_connection_string="sqlite+aiosqlite:///:memory:")
        engine = EngineFactory.from_connection_string(settings.db_connection_string)
        assert engine is not None

    @pytest.mark.asyncio
    async def test_full_db_workflow(self):
        """Test complete workflow: settings -> engine -> session -> query."""
        from sqlalchemy import text

        from azurerbac.core.db import EngineFactory, create_sessionmaker
        from azurerbac.settings import Settings

        settings = Settings(db_connection_string="sqlite+aiosqlite:///:memory:")
        engine = EngineFactory.from_connection_string(settings.db_connection_string)
        sessionmaker = create_sessionmaker(engine)

        async with sessionmaker() as session:
            result = await session.execute(text("SELECT 42 as answer"))
            row = result.scalar()
            assert row == 42


class TestUtils:
    """Tests for core.utils module."""

    @pytest.mark.parametrize(
        ("input_uuid", "expected"),
        [
            pytest.param(
                "550e8400-e29b-41d4-a716-446655440000",
                "550e8400-e29b-41d4-a716-446655440000",
                id="standard_format",
            ),
            pytest.param(
                "550E8400-E29B-41D4-A716-446655440000",
                "550e8400-e29b-41d4-a716-446655440000",
                id="uppercase",
            ),
            pytest.param(
                "  550e8400-e29b-41d4-a716-446655440000  ",
                "550e8400-e29b-41d4-a716-446655440000",
                id="with_whitespace",
            ),
            pytest.param("not-a-uuid", None, id="invalid_string"),
            pytest.param("12345", None, id="short_string"),
            pytest.param("", None, id="empty_string"),
        ],
    )
    def test_normalize_uuid_or_none(self, input_uuid, expected):
        """Test normalize_uuid_or_none with various inputs."""
        from azurerbac.core.utils import normalize_uuid_or_none

        assert normalize_uuid_or_none(input_uuid) == expected

    @pytest.mark.parametrize(
        ("input_dt", "expected_tzinfo"),
        [
            pytest.param(None, None, id="none_input"),
            pytest.param(
                dt.datetime(2023, 1, 15, 12, 30, 0),
                dt.UTC,
                id="naive_datetime",
            ),
            pytest.param(
                dt.datetime(2023, 1, 15, 12, 30, 0, tzinfo=dt.UTC),
                dt.UTC,
                id="aware_datetime",
            ),
        ],
    )
    def test_ensure_utc(self, input_dt, expected_tzinfo):
        """Test ensure_utc with various inputs."""
        from azurerbac.core.utils import ensure_utc

        result = ensure_utc(input_dt)
        if input_dt is None:
            assert result is None
        else:
            assert result is not None
            assert result.tzinfo == expected_tzinfo

    # format_iso_z parametrized tests
    @pytest.mark.parametrize(
        ("input_dt", "expected"),
        [
            pytest.param(None, None, id="none_returns_none"),
            pytest.param(
                dt.datetime(2025, 12, 17, 9, 58, 12, 949000, tzinfo=dt.UTC),
                "2025-12-17T09:58:12.949Z",
                id="utc_aware_datetime",
            ),
            pytest.param(
                dt.datetime(2025, 12, 17, 9, 58, 12, 949000),
                "2025-12-17T09:58:12.949Z",
                id="naive_datetime_gets_utc",
            ),
            pytest.param(
                dt.datetime(2025, 12, 17, 9, 58, 12, tzinfo=dt.UTC),
                "2025-12-17T09:58:12.000Z",
                id="zero_microseconds",
            ),
            pytest.param(
                dt.datetime(2025, 12, 17, 9, 58, 12, 123456, tzinfo=dt.UTC),
                "2025-12-17T09:58:12.123Z",
                id="microseconds_truncated_to_milliseconds",
            ),
        ],
    )
    def test_format_iso_z(self, input_dt, expected):
        """Test format_iso_z produces ISO 8601 with 'Z' suffix and milliseconds precision."""
        from azurerbac.core.utils import format_iso_z

        assert format_iso_z(input_dt) == expected

    def test_format_iso_z_replaces_plus_suffix(self):
        """Test format_iso_z correctly replaces +00:00 with Z."""
        from azurerbac.core.utils import format_iso_z

        aware = dt.datetime(2025, 12, 17, 9, 58, 12, 949000, tzinfo=dt.UTC)
        # Verify isoformat produces +00:00
        assert aware.isoformat().endswith("+00:00")
        # Verify format_iso_z produces Z
        result = format_iso_z(aware)
        assert result is not None
        assert result.endswith("Z")
        assert "+00:00" not in result


# =============================================================================
# Pattern Utility Tests
# =============================================================================


class TestPatternUtilities:
    """Tests for core/patterns.py utility functions."""

    @pytest.mark.parametrize(
        "pattern,expected",
        [
            pytest.param("Microsoft.Storage/*", True, id="trailing_wildcard"),
            pytest.param("*/read", True, id="leading_wildcard"),
            pytest.param("Microsoft.*/read", True, id="middle_wildcard"),
            pytest.param("*", True, id="universal_wildcard"),
            pytest.param("Microsoft.Storage/read", False, id="no_wildcard"),
            pytest.param("Microsoft.Storage/storageAccounts/read", False, id="explicit_path"),
        ],
    )
    def test_is_wildcard_pattern(self, pattern: str, expected: bool):
        """Test is_wildcard_pattern detection."""
        from azurerbac.core.patterns import is_wildcard_pattern

        assert is_wildcard_pattern(pattern) == expected

    @pytest.mark.parametrize(
        "patterns,all_ops,expected_count",
        [
            pytest.param(
                ["Microsoft.Storage/*"],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                    "microsoft.compute/virtualmachines/read",
                },
                2,
                id="prefix_wildcard",
            ),
            pytest.param(
                ["*/read"],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.compute/virtualmachines/read",
                    "microsoft.storage/storageaccounts/write",
                },
                2,
                id="suffix_wildcard",
            ),
            pytest.param(
                ["*"],
                {"op1", "op2", "op3"},
                3,
                id="universal_wildcard_returns_all",
            ),
            pytest.param(
                ["microsoft.storage/storageaccounts/read"],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                },
                1,
                id="explicit_case_insensitive_match",
            ),
            pytest.param(
                ["Microsoft.Storage/storageAccounts/read"],  # Mixed case
                {
                    "microsoft.storage/storageaccounts/read",  # Lowercase
                    "microsoft.storage/storageaccounts/write",
                },
                1,
                id="explicit_mixed_case_match",
            ),
            pytest.param(
                ["nonexistent/operation"],
                {"op1", "op2"},
                0,
                id="no_match_returns_empty",
            ),
            pytest.param(
                [],
                {"op1", "op2"},
                0,
                id="empty_patterns_returns_empty",
            ),
        ],
    )
    def test_expand_patterns_to_operations(
        self, patterns: list[str], all_ops: set[str], expected_count: int
    ):
        """Test expand_patterns_to_operations with various patterns."""
        from azurerbac.core.patterns import expand_patterns_to_operations

        result = expand_patterns_to_operations(patterns, all_ops)
        assert len(result) == expected_count

    def test_expand_patterns_with_multiple_patterns(self):
        """Test expanding multiple patterns at once."""
        from azurerbac.core.patterns import expand_patterns_to_operations

        patterns = ["Microsoft.Storage/*", "Microsoft.Compute/*"]
        all_ops = {
            "microsoft.storage/storageaccounts/read",
            "microsoft.compute/virtualmachines/start",
            "microsoft.network/virtualnetworks/read",
        }

        result = expand_patterns_to_operations(patterns, all_ops)
        assert len(result) == 2
        assert "microsoft.storage/storageaccounts/read" in result
        assert "microsoft.compute/virtualmachines/start" in result
        assert "microsoft.network/virtualnetworks/read" not in result


# =============================================================================
# Enum Tests
# =============================================================================
