"""Comprehensive tests for the core modules: config, db, and logging_setup."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from azurerbac.settings import Settings


class TestSettings:
    """Tests for Settings model."""

    def test_custom_values(self):
        """Test Settings accepts custom values."""
        settings = Settings(
            azure_subscription_id="test-sub-123",
            roles_poll_interval_seconds=300,
            db_connection_string="postgresql+asyncpg://user:pass@host/db",
        )
        assert settings.azure_subscription_id == "test-sub-123"
        assert settings.roles_poll_interval_seconds == 300
        assert settings.db_connection_string == "postgresql+asyncpg://user:pass@host/db"

    def test_partial_override(self):
        """Test Settings allows partial override of defaults."""
        settings = Settings(roles_poll_interval_seconds=120)
        assert settings.azure_subscription_id is None
        assert settings.roles_poll_interval_seconds == 120
        assert settings.db_connection_string == "sqlite+aiosqlite:///./azurerbac.db"

    def test_invalid_poll_interval_type(self):
        """Test Settings rejects invalid types."""
        with pytest.raises(ValidationError):
            Settings(roles_poll_interval_seconds="not-a-number")


class TestGetSettings:
    """Tests for get_settings function."""

    _CONFIG_ENV_KEYS: ClassVar[list[str]] = [
        "AZURE_SUBSCRIPTION_ID",
        "DB_CONNECTION_STRING",
        "ROLES_POLL_INTERVAL_SECONDS",
        "OPERATIONS_POLL_INTERVAL_SECONDS",
        "ROLE_SCAN_ENABLED",
        "OPERATIONS_SCAN_ENABLED",
        "RUN_SCAN_ON_STARTUP",
        "RUN_OPERATIONS_SCAN_ON_STARTUP",
    ]

    def test_get_settings_defaults(self):
        """Test get_settings with no environment variables."""
        # Clear relevant env vars
        env_backup = {}
        for key in self._CONFIG_ENV_KEYS:
            env_backup[key] = os.environ.pop(key, None)

        try:
            settings = Settings.get()
            assert settings.azure_subscription_id is None
            assert settings.roles_poll_interval_seconds == 600
            assert settings.db_connection_string == "sqlite+aiosqlite:///./azurerbac.db"
        finally:
            # Restore env vars
            for key, value in env_backup.items():
                if value is not None:
                    os.environ[key] = value

    def test_get_settings_from_env(self):
        """Test get_settings reads from environment."""
        with patch.dict(
            os.environ,
            {
                "AZURE_SUBSCRIPTION_ID": "env-sub-456",
                "ROLES_POLL_INTERVAL_SECONDS": "120",
                "DB_CONNECTION_STRING": "postgresql+asyncpg://env@host/db",
            },
            clear=True,
        ):
            settings = Settings.get()
            assert settings.azure_subscription_id == "env-sub-456"
            assert settings.roles_poll_interval_seconds == 120
            assert settings.db_connection_string == "postgresql+asyncpg://env@host/db"

    def test_get_settings_partial_env(self):
        """Test get_settings with only some env vars set."""
        # Clear all and set only one
        env_backup = {}
        for key in self._CONFIG_ENV_KEYS:
            env_backup[key] = os.environ.pop(key, None)

        try:
            os.environ["AZURE_SUBSCRIPTION_ID"] = "partial-sub"
            settings = Settings.get()
            assert settings.azure_subscription_id == "partial-sub"
            assert settings.roles_poll_interval_seconds == 600  # Default
            assert settings.db_connection_string == "sqlite+aiosqlite:///./azurerbac.db"  # Default
        finally:
            os.environ.pop("AZURE_SUBSCRIPTION_ID", None)
            for key, value in env_backup.items():
                if value is not None:
                    os.environ[key] = value


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
            {"WEBSITE_SITE_NAME": "my-app", "APPLICATIONINSIGHTS_CONNECTION_STRING": ""},
            clear=False,
        ):
            importlib.reload(logging_module)
            logging_module._configured = False
            logging.getLogger().handlers = []

            logging_module.configure_logging("azure_test")

            root = logging.getLogger()
            # Should have no FileHandler in Azure
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert len(file_handlers) == 0

        # Reload to restore local environment
        os.environ.pop("WEBSITE_SITE_NAME", None)
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

    def test_credential_filter_redacts_bearer_token(self):
        """Test that Bearer tokens are redacted from logs."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Request with Bearer token123abc",
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert "[REDACTED" in record.msg
        assert "token123abc" not in record.msg

    def test_credential_filter_redacts_password(self):
        """Test that passwords are redacted from logs."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Connection string with password=secret123",
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert "[REDACTED" in record.msg
        assert "secret123" not in record.msg

    def test_credential_filter_redacts_api_key(self):
        """Test that API keys are redacted from logs."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Using api_key=myapikey123",
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert "[REDACTED" in record.msg
        assert "myapikey123" not in record.msg

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

    def test_credential_filter_case_insensitive(self):
        """Test that credential filter is case-insensitive."""
        import azurerbac.telemetry.logging as logging_module

        filter_instance = logging_module._CredentialFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Has BEARER TOKEN123 uppercase",
            args=(),
            exc_info=None,
        )
        filter_instance.filter(record)
        assert "[REDACTED" in record.msg


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

    def test_normalize_uuid_or_none_valid_uuid(self):
        """Test normalize_uuid_or_none with valid UUIDs."""
        from azurerbac.core.utils import normalize_uuid_or_none

        # Standard format
        result = normalize_uuid_or_none("550e8400-e29b-41d4-a716-446655440000")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

        # Uppercase
        result = normalize_uuid_or_none("550E8400-E29B-41D4-A716-446655440000")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

        # With whitespace
        result = normalize_uuid_or_none("  550e8400-e29b-41d4-a716-446655440000  ")
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_normalize_uuid_or_none_invalid(self):
        """Test normalize_uuid_or_none with invalid inputs."""
        from azurerbac.core.utils import normalize_uuid_or_none

        assert normalize_uuid_or_none("not-a-uuid") is None
        assert normalize_uuid_or_none("12345") is None
        assert normalize_uuid_or_none("") is None

    def test_ensure_utc_with_none(self):
        """Test ensure_utc with None input."""
        from azurerbac.core.utils import ensure_utc

        assert ensure_utc(None) is None

    def test_ensure_utc_with_naive_datetime(self):
        """Test ensure_utc with naive datetime."""
        import datetime as dt

        from azurerbac.core.utils import ensure_utc

        naive = dt.datetime(2023, 1, 15, 12, 30, 0)
        result = ensure_utc(naive)
        assert result is not None
        assert result.tzinfo == dt.UTC

    def test_ensure_utc_with_aware_datetime(self):
        """Test ensure_utc with already aware datetime."""
        import datetime as dt

        from azurerbac.core.utils import ensure_utc

        aware = dt.datetime(2023, 1, 15, 12, 30, 0, tzinfo=dt.UTC)
        result = ensure_utc(aware)
        assert result is aware  # Should return same object

    def test_ensure_utc_or_min_with_none(self):
        """Test ensure_utc_or_min returns datetime.min for None."""
        import datetime as dt

        from azurerbac.core.utils import ensure_utc_or_min

        result = ensure_utc_or_min(None)
        assert result == dt.datetime.min.replace(tzinfo=dt.UTC)

    def test_ensure_utc_or_min_with_datetime(self):
        """Test ensure_utc_or_min preserves datetime."""
        import datetime as dt

        from azurerbac.core.utils import ensure_utc_or_min

        aware = dt.datetime(2023, 1, 15, 12, 30, 0, tzinfo=dt.UTC)
        result = ensure_utc_or_min(aware)
        assert result == aware
