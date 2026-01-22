"""Application configuration via Pydantic Settings."""

from __future__ import annotations

import logging
import os
from typing import Final

from pydantic import BaseModel, Field

from azurerbac.core.singleton import ThreadSafeSingleton

logger = logging.getLogger(__name__)


class EnvVars:
    """Environment variable names."""

    AZURE_SUBSCRIPTION_ID: Final = "AZURE_SUBSCRIPTION_ID"
    WEBSITE_SITE_NAME: Final = "WEBSITE_SITE_NAME"
    ROLES_POLL_INTERVAL_SECONDS: Final = "ROLES_POLL_INTERVAL_SECONDS"
    OPERATIONS_POLL_INTERVAL_SECONDS: Final = "OPERATIONS_POLL_INTERVAL_SECONDS"
    ROLE_SCAN_ENABLED: Final = "ROLE_SCAN_ENABLED"
    OPERATIONS_SCAN_ENABLED: Final = "OPERATIONS_SCAN_ENABLED"
    RUN_SCAN_ON_STARTUP: Final = "RUN_SCAN_ON_STARTUP"
    RUN_OPERATIONS_SCAN_ON_STARTUP: Final = "RUN_OPERATIONS_SCAN_ON_STARTUP"
    DB_CONNECTION_STRING: Final = "DB_CONNECTION_STRING"
    USE_MANAGED_IDENTITY: Final = "USE_MANAGED_IDENTITY"
    MSI_CONNECTION_STRING: Final = "MSI_CONNECTION_STRING"
    MSI_DB_HOST: Final = "MSI_DB_HOST"
    MSI_DB_PORT: Final = "MSI_DB_PORT"
    MSI_DB_NAME: Final = "MSI_DB_NAME"
    MSI_DB_USER: Final = "MSI_DB_USER"
    OLLAMA_BASE_URL: Final = "OLLAMA_BASE_URL"
    OLLAMA_MODEL: Final = "OLLAMA_MODEL"
    LOG_LEVEL: Final = "LOG_LEVEL"
    CACHE_BACKEND: Final = "CACHE_BACKEND"
    CACHE_CHECK_INTERVAL_SECONDS: Final = "CACHE_CHECK_INTERVAL_SECONDS"
    DB_REBUILD_INTERVAL_SECONDS: Final = "DB_REBUILD_INTERVAL_SECONDS"
    AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS: Final = "AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS"
    APPLICATIONINSIGHTS_CONNECTION_STRING: Final = "APPLICATIONINSIGHTS_CONNECTION_STRING"
    IS_PRODUCTION: Final = "IS_PRODUCTION"
    PYTEST_CURRENT_TEST: Final = "PYTEST_CURRENT_TEST"
    USE_RBAC_API: Final = "USE_RBAC_API"
    MCP_SERVER_ENABLED: Final = "MCP_SERVER_ENABLED"


_BOOL_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "y", "on"})


def is_running_in_azure() -> bool:
    """Check if running in Azure App Service."""
    return bool(os.getenv(EnvVars.WEBSITE_SITE_NAME))


def is_running_in_pytest() -> bool:
    """Check if running inside pytest."""
    return bool(os.getenv(EnvVars.PYTEST_CURRENT_TEST))


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return value.strip().lower() in _BOOL_TRUE_VALUES if value else default


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


class Settings(BaseModel):
    """Application settings from environment variables."""

    azure_subscription_id: str | None = None
    roles_poll_interval_seconds: int = Field(default=600, gt=0)
    operations_poll_interval_seconds: int = Field(default=86400, gt=0)
    role_scan_enabled: bool = True
    operations_scan_enabled: bool = True
    run_roles_scan_on_startup: bool = True
    run_operations_scan_on_startup: bool = True
    db_connection_string: str = "sqlite+aiosqlite:///./azurerbac.db"
    use_managed_identity: bool = False
    msi_db_host: str = ""
    msi_db_port: int = 5432
    msi_db_name: str = ""
    msi_db_user: str = ""
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen-rbac-v5"
    log_level: str = "INFO"
    cache_backend: str = "file"
    cache_check_interval_seconds: int = Field(default=30, gt=0)
    db_rebuild_interval_seconds: int = Field(default=3600, gt=0)
    enable_embeddings_in_tests: bool = False
    app_insights_connection_string: str = ""
    is_production: bool = False
    use_rbac_api: bool = False
    mcp_server_enabled: bool = True

    @property
    def environment_name(self) -> str:
        return "production" if self.is_production else "staging"

    @classmethod
    def reset(cls) -> None:
        _settings.reset()

    @classmethod
    def get(cls) -> Settings:
        return _settings.get()


def _load_settings() -> Settings:
    app_insights = (
        os.getenv(EnvVars.APPLICATIONINSIGHTS_CONNECTION_STRING, "")
        if is_running_in_azure()
        else ""
    )
    return Settings(
        azure_subscription_id=os.getenv(EnvVars.AZURE_SUBSCRIPTION_ID),
        roles_poll_interval_seconds=_get_int(EnvVars.ROLES_POLL_INTERVAL_SECONDS, 600),
        operations_poll_interval_seconds=_get_int(EnvVars.OPERATIONS_POLL_INTERVAL_SECONDS, 86400),
        role_scan_enabled=_get_bool(EnvVars.ROLE_SCAN_ENABLED, True),
        operations_scan_enabled=_get_bool(EnvVars.OPERATIONS_SCAN_ENABLED, True),
        run_roles_scan_on_startup=_get_bool(EnvVars.RUN_SCAN_ON_STARTUP, True),
        run_operations_scan_on_startup=_get_bool(EnvVars.RUN_OPERATIONS_SCAN_ON_STARTUP, True),
        db_connection_string=os.getenv(
            EnvVars.DB_CONNECTION_STRING, "sqlite+aiosqlite:///./azurerbac.db"
        ),
        use_managed_identity=_get_bool(EnvVars.USE_MANAGED_IDENTITY, False),
        msi_db_host=os.getenv(EnvVars.MSI_DB_HOST, ""),
        msi_db_port=_get_int(EnvVars.MSI_DB_PORT, 5432),
        msi_db_name=os.getenv(EnvVars.MSI_DB_NAME, ""),
        msi_db_user=os.getenv(EnvVars.MSI_DB_USER, ""),
        ollama_base_url=os.getenv(EnvVars.OLLAMA_BASE_URL, "http://localhost:11434"),
        ollama_model=os.getenv(EnvVars.OLLAMA_MODEL, "qwen-rbac-v5"),
        log_level=os.getenv(EnvVars.LOG_LEVEL, "INFO").upper(),
        cache_check_interval_seconds=_get_int(EnvVars.CACHE_CHECK_INTERVAL_SECONDS, 30),
        db_rebuild_interval_seconds=_get_int(EnvVars.DB_REBUILD_INTERVAL_SECONDS, 3600),
        enable_embeddings_in_tests=_get_bool(EnvVars.AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS, False),
        app_insights_connection_string=app_insights,
        is_production=_get_bool(EnvVars.IS_PRODUCTION, False),
        use_rbac_api=_get_bool(EnvVars.USE_RBAC_API, False),
        mcp_server_enabled=_get_bool(EnvVars.MCP_SERVER_ENABLED, True),
    )


_settings: ThreadSafeSingleton[Settings] = ThreadSafeSingleton(factory=_load_settings)


def get_settings() -> Settings:
    """Get the global settings instance."""
    return _settings.get()  # type: ignore[return-value]


def reset_settings() -> None:
    """Reset settings singleton (for testing)."""
    _settings.reset()
