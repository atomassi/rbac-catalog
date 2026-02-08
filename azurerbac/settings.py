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

    APP_ENVIRONMENT_NAME: Final = "APP_ENVIRONMENT_NAME"
    ROLES_POLL_INTERVAL_SECONDS: Final = "ROLES_POLL_INTERVAL_SECONDS"
    OPERATIONS_POLL_INTERVAL_SECONDS: Final = "OPERATIONS_POLL_INTERVAL_SECONDS"
    ROLE_SCAN_ENABLED: Final = "ROLE_SCAN_ENABLED"
    OPERATIONS_SCAN_ENABLED: Final = "OPERATIONS_SCAN_ENABLED"
    RUN_SCAN_ON_STARTUP: Final = "RUN_SCAN_ON_STARTUP"
    RUN_OPERATIONS_SCAN_ON_STARTUP: Final = "RUN_OPERATIONS_SCAN_ON_STARTUP"
    DB_CONNECTION_STRING: Final = "DB_CONNECTION_STRING"
    USE_MANAGED_IDENTITY: Final = "USE_MANAGED_IDENTITY"
    MSI_DB_HOST: Final = "MSI_DB_HOST"
    MSI_DB_PORT: Final = "MSI_DB_PORT"
    MSI_DB_NAME: Final = "MSI_DB_NAME"
    MSI_DB_USER: Final = "MSI_DB_USER"
    OLLAMA_BASE_URL: Final = "OLLAMA_BASE_URL"
    OLLAMA_MODEL: Final = "OLLAMA_MODEL"
    LOG_LEVEL: Final = "LOG_LEVEL"
    DB_REBUILD_INTERVAL_SECONDS: Final = "DB_REBUILD_INTERVAL_SECONDS"
    AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS: Final = "AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS"
    APPLICATIONINSIGHTS_CONNECTION_STRING: Final = "APPLICATIONINSIGHTS_CONNECTION_STRING"
    PYTEST_CURRENT_TEST: Final = "PYTEST_CURRENT_TEST"
    USE_RBAC_API: Final = "USE_RBAC_API"
    MCP_SERVER_ENABLED: Final = "MCP_SERVER_ENABLED"


_BOOL_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "y", "on"})
_VALID_ENVIRONMENTS: Final = frozenset({"production", "staging", "ppe"})


def _get_environment_name() -> str:
    """Get environment name from APP_ENVIRONMENT_NAME env var."""
    value = os.getenv(EnvVars.APP_ENVIRONMENT_NAME, "local").lower().strip()
    return value if value in _VALID_ENVIRONMENTS else "local"


def is_running_in_pytest() -> bool:
    """Check if running inside pytest."""
    return bool(os.getenv(EnvVars.PYTEST_CURRENT_TEST))


def _set_bool(kwargs: dict[str, object], key: str, env_var: str) -> None:
    if (value := os.getenv(env_var)) is not None and (stripped := value.strip()):
        kwargs[key] = stripped.lower() in _BOOL_TRUE_VALUES


def _set_int(kwargs: dict[str, object], key: str, env_var: str) -> None:
    if (value := os.getenv(env_var)) is not None and (stripped := value.strip()):
        kwargs[key] = int(stripped)


def _set_str(kwargs: dict[str, object], key: str, env_var: str) -> None:
    if (value := os.getenv(env_var)) is not None and (stripped := value.strip()):
        kwargs[key] = stripped


class Settings(BaseModel):
    """Application settings from environment variables."""

    roles_poll_interval_seconds: int = Field(default=7200, gt=0)
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
    db_rebuild_interval_seconds: int = Field(default=21600, gt=0)
    enable_embeddings_in_tests: bool = False
    app_insights_connection_string: str = ""
    environment_name: str = "local"
    use_rbac_api: bool = True
    mcp_server_enabled: bool = True

    @property
    def is_deployed(self) -> bool:
        """Check if running in any deployed environment (not local)."""
        return self.environment_name in _VALID_ENVIRONMENTS

    @classmethod
    def reset(cls) -> None:
        _settings.reset()

    @classmethod
    def get(cls) -> Settings:
        return _settings.get()


def _load_settings() -> Settings:
    env_name = _get_environment_name()
    app_insights = (
        os.getenv(EnvVars.APPLICATIONINSIGHTS_CONNECTION_STRING, "")
        if env_name in _VALID_ENVIRONMENTS
        else ""
    )

    # Build kwargs only for env vars that are actually set, letting
    # the Settings model defaults handle everything else (single source of truth).
    kwargs: dict[str, object] = {
        "environment_name": env_name,
        "app_insights_connection_string": app_insights,
    }

    _set_int(kwargs, "roles_poll_interval_seconds", EnvVars.ROLES_POLL_INTERVAL_SECONDS)
    _set_int(kwargs, "operations_poll_interval_seconds", EnvVars.OPERATIONS_POLL_INTERVAL_SECONDS)
    _set_bool(kwargs, "role_scan_enabled", EnvVars.ROLE_SCAN_ENABLED)
    _set_bool(kwargs, "operations_scan_enabled", EnvVars.OPERATIONS_SCAN_ENABLED)
    _set_bool(kwargs, "run_roles_scan_on_startup", EnvVars.RUN_SCAN_ON_STARTUP)
    _set_bool(kwargs, "run_operations_scan_on_startup", EnvVars.RUN_OPERATIONS_SCAN_ON_STARTUP)
    _set_str(kwargs, "db_connection_string", EnvVars.DB_CONNECTION_STRING)
    _set_bool(kwargs, "use_managed_identity", EnvVars.USE_MANAGED_IDENTITY)
    _set_str(kwargs, "msi_db_host", EnvVars.MSI_DB_HOST)
    _set_int(kwargs, "msi_db_port", EnvVars.MSI_DB_PORT)
    _set_str(kwargs, "msi_db_name", EnvVars.MSI_DB_NAME)
    _set_str(kwargs, "msi_db_user", EnvVars.MSI_DB_USER)
    _set_str(kwargs, "ollama_base_url", EnvVars.OLLAMA_BASE_URL)
    _set_str(kwargs, "ollama_model", EnvVars.OLLAMA_MODEL)
    _set_int(kwargs, "db_rebuild_interval_seconds", EnvVars.DB_REBUILD_INTERVAL_SECONDS)
    _set_bool(kwargs, "enable_embeddings_in_tests", EnvVars.AZURERBAC_ENABLE_EMBEDDINGS_IN_TESTS)
    _set_bool(kwargs, "use_rbac_api", EnvVars.USE_RBAC_API)
    _set_bool(kwargs, "mcp_server_enabled", EnvVars.MCP_SERVER_ENABLED)

    log_level = os.getenv(EnvVars.LOG_LEVEL)
    if log_level:
        kwargs["log_level"] = log_level.upper()

    return Settings(**kwargs)  # type: ignore[arg-type]


_settings: ThreadSafeSingleton[Settings] = ThreadSafeSingleton(factory=_load_settings)
