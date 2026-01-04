"""Application configuration via Pydantic Settings."""

from __future__ import annotations

import logging
import os
from typing import Final

from pydantic import BaseModel, Field

from azurerbac.core.singleton import ThreadSafeSingleton

logger = logging.getLogger(__name__)


class EnvVars:
    """Environment variable names for configuration."""

    # Azure
    AZURE_SUBSCRIPTION_ID: Final = "AZURE_SUBSCRIPTION_ID"
    WEBSITE_SITE_NAME: Final = "WEBSITE_SITE_NAME"

    # Background jobs
    ROLES_POLL_INTERVAL_SECONDS: Final = "ROLES_POLL_INTERVAL_SECONDS"
    OPERATIONS_POLL_INTERVAL_SECONDS: Final = "OPERATIONS_POLL_INTERVAL_SECONDS"
    ROLE_SCAN_ENABLED: Final = "ROLE_SCAN_ENABLED"
    OPERATIONS_SCAN_ENABLED: Final = "OPERATIONS_SCAN_ENABLED"
    RUN_SCAN_ON_STARTUP: Final = "RUN_SCAN_ON_STARTUP"
    RUN_OPERATIONS_SCAN_ON_STARTUP: Final = "RUN_OPERATIONS_SCAN_ON_STARTUP"

    # Database
    DB_CONNECTION_STRING: Final = "DB_CONNECTION_STRING"
    USE_MANAGED_IDENTITY: Final = "USE_MANAGED_IDENTITY"
    MSI_CONNECTION_STRING: Final = "MSI_CONNECTION_STRING"

    # MSI connection settings
    MSI_DB_HOST: Final = "MSI_DB_HOST"
    MSI_DB_PORT: Final = "MSI_DB_PORT"
    MSI_DB_NAME: Final = "MSI_DB_NAME"
    MSI_DB_USER: Final = "MSI_DB_USER"

    # Ollama LLM
    OLLAMA_BASE_URL: Final = "OLLAMA_BASE_URL"
    OLLAMA_MODEL: Final = "OLLAMA_MODEL"

    # Logging and debug
    LOG_LEVEL: Final = "LOG_LEVEL"
    AZURERBAC_DEBUG: Final = "AZURERBAC_DEBUG"

    # Cache
    CACHE_DIR: Final = "CACHE_DIR"
    CACHE_CHECK_INTERVAL_SECONDS: Final = "CACHE_CHECK_INTERVAL_SECONDS"
    DB_REBUILD_INTERVAL_SECONDS: Final = "DB_REBUILD_INTERVAL_SECONDS"

    # Embeddings
    AZURERBAC_DISABLE_EMBEDDINGS: Final = "AZURERBAC_DISABLE_EMBEDDINGS"
    AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS: Final = "AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS"

    # Telemetry
    APPLICATIONINSIGHTS_CONNECTION_STRING: Final = "APPLICATIONINSIGHTS_CONNECTION_STRING"

    # Environment
    IS_PRODUCTION: Final = "IS_PRODUCTION"

    # Testing (set by pytest)
    PYTEST_CURRENT_TEST: Final = "PYTEST_CURRENT_TEST"


_BOOL_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "y", "on"})


def is_running_in_azure() -> bool:
    """Check if running in Azure App Service."""
    return bool(os.getenv(EnvVars.WEBSITE_SITE_NAME))


def is_running_in_pytest() -> bool:
    """Check if running under pytest."""
    return bool(os.getenv(EnvVars.PYTEST_CURRENT_TEST))


def _get_bool(name: str, default: bool) -> bool:
    """Parse boolean from environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _BOOL_TRUE_VALUES


def _get_int(name: str, default: int) -> int:
    """Parse integer from environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


class Settings(BaseModel):
    """Application settings loaded from environment variables.

    Use get_settings() or Settings.get() to get the singleton instance.
    Use reset_settings() or Settings.reset() to force reload.
    """

    # Azure configuration
    azure_subscription_id: str | None = None

    # Background job polling intervals
    roles_poll_interval_seconds: int = Field(default=600, gt=0)
    operations_poll_interval_seconds: int = Field(default=86400, gt=0)

    # Background job enable flags
    role_scan_enabled: bool = True
    operations_scan_enabled: bool = True

    # Startup behavior
    run_roles_scan_on_startup: bool = True
    run_operations_scan_on_startup: bool = True

    # Database
    db_connection_string: str = "sqlite+aiosqlite:///./azurerbac.db"
    use_managed_identity: bool = False

    # If set, these take precedence over parsing the connection string
    msi_db_host: str = ""
    msi_db_port: int = 5432
    msi_db_name: str = ""
    msi_db_user: str = ""

    # Ollama LLM configuration
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen-rbac-v5"

    # Logging
    log_level: str = "INFO"

    # Debug mode
    debug: bool = False

    # Cache directory (optional override)
    cache_dir: str | None = None

    # Cache timing
    cache_check_interval_seconds: int = Field(default=30, gt=0)
    db_rebuild_interval_seconds: int = Field(default=3600, gt=0)

    # Embeddings configuration
    disable_embeddings: bool = False
    enable_embeddings_in_tests: bool = False

    # Telemetry
    app_insights_connection_string: str = ""
    telemetry_flush_timeout_ms: int = Field(default=10000, gt=0)

    # Environment
    is_production: bool = False

    @property
    def environment_name(self) -> str:
        """Return 'production' or 'staging' based on is_production flag."""
        return "production" if self.is_production else "staging"

    @classmethod
    def reset(cls) -> None:
        """Reset settings singleton to force reload from environment."""
        _settings.reset()

    @classmethod
    def get(cls) -> Settings:
        """Get settings singleton instance (thread-safe)."""
        return _settings.get()


def _load_settings() -> Settings:
    """Load settings from environment variables.

    Each setting can be overridden by setting the corresponding environment
    variable in Azure App Service Configuration or locally via .env file.
    Environment variables are loaded by python-dotenv in app startup.
    """
    # Telemetry connection string - only in production
    app_insights = ""
    if is_running_in_azure():
        app_insights = os.getenv(EnvVars.APPLICATIONINSIGHTS_CONNECTION_STRING, "")

    return Settings(
        # Azure
        azure_subscription_id=os.getenv(EnvVars.AZURE_SUBSCRIPTION_ID),
        # Polling intervals
        roles_poll_interval_seconds=_get_int(EnvVars.ROLES_POLL_INTERVAL_SECONDS, 600),
        operations_poll_interval_seconds=_get_int(EnvVars.OPERATIONS_POLL_INTERVAL_SECONDS, 86400),
        # Enable flags
        role_scan_enabled=_get_bool(EnvVars.ROLE_SCAN_ENABLED, True),
        operations_scan_enabled=_get_bool(EnvVars.OPERATIONS_SCAN_ENABLED, True),
        # Startup behavior
        run_roles_scan_on_startup=_get_bool(EnvVars.RUN_SCAN_ON_STARTUP, True),
        run_operations_scan_on_startup=_get_bool(EnvVars.RUN_OPERATIONS_SCAN_ON_STARTUP, True),
        # Database
        db_connection_string=os.getenv(
            EnvVars.DB_CONNECTION_STRING, "sqlite+aiosqlite:///./azurerbac.db"
        ),
        use_managed_identity=_get_bool(EnvVars.USE_MANAGED_IDENTITY, False),
        # MSI connection settings
        msi_db_host=os.getenv(EnvVars.MSI_DB_HOST, ""),
        msi_db_port=_get_int(EnvVars.MSI_DB_PORT, 5432),
        msi_db_name=os.getenv(EnvVars.MSI_DB_NAME, ""),
        msi_db_user=os.getenv(EnvVars.MSI_DB_USER, ""),
        # Ollama
        ollama_base_url=os.getenv(EnvVars.OLLAMA_BASE_URL, "http://localhost:11434"),
        ollama_model=os.getenv(EnvVars.OLLAMA_MODEL, "qwen-rbac-v5"),
        # Logging
        log_level=os.getenv(EnvVars.LOG_LEVEL, "INFO").upper(),
        # Debug
        debug=_get_bool(EnvVars.AZURERBAC_DEBUG, False),
        # Cache
        cache_dir=os.getenv(EnvVars.CACHE_DIR),
        cache_check_interval_seconds=_get_int(EnvVars.CACHE_CHECK_INTERVAL_SECONDS, 30),
        db_rebuild_interval_seconds=_get_int(EnvVars.DB_REBUILD_INTERVAL_SECONDS, 3600),
        # Embeddings
        disable_embeddings=_get_bool(EnvVars.AZURERBAC_DISABLE_EMBEDDINGS, False),
        enable_embeddings_in_tests=_get_bool(EnvVars.AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS, False),
        # Telemetry
        app_insights_connection_string=app_insights,
        # Environment
        is_production=_get_bool(EnvVars.IS_PRODUCTION, False),
    )


# Module-level singleton
_settings: ThreadSafeSingleton[Settings] = ThreadSafeSingleton(factory=_load_settings)


def get_settings() -> Settings:
    """Get the global settings instance (thread-safe singleton)."""
    return _settings.get()  # type: ignore[return-value]


def reset_settings() -> None:
    """Reset settings singleton to force reload from environment."""
    _settings.reset()
