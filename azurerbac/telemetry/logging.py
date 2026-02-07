"""Logging configuration for Application Insights and file output."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from azurerbac.settings import Settings

LOGS_DIR: Final = Path(__file__).parent.parent.parent / "logs"

_logger = logging.getLogger(__name__)
_configured = False
_configured_log_file: str | None = None
_configured_component: str | None = None

_SENSITIVE_PATTERNS: Final = (
    "Bearer ",
    "Authorization:",
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "api_key",
    "apikey",
)


class _CredentialFilter(logging.Filter):
    """Redact sensitive information from logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        if isinstance(rendered, str):
            rendered_lower = rendered.lower()
            for pattern in _SENSITIVE_PATTERNS:
                if pattern.lower() in rendered_lower:
                    record.msg = "[REDACTED - contains sensitive data]"
                    record.args = ()
                    break
        return True


class _EnvironmentFilter(logging.Filter):
    """Add environment attribute to log records."""

    def __init__(self, environment: str) -> None:
        super().__init__()
        self.environment = environment

    def filter(self, record: logging.LogRecord) -> bool:
        record.environment = self.environment
        return True


def configure_logging(component: str = "app", level: int = logging.INFO) -> str | None:
    """Configure logging based on environment.

    Local: logs to azurerbac/logs/{component}.log
    Production: logs to Application Insights (traces table)

    Args:
        component: Component name for log file ("ux" or "worker")
        level: Logging level (default: INFO)

    Returns:
        Log file path if local, None if production
    """
    global _configured, _configured_component, _configured_log_file
    if _configured:
        return _configured_log_file
    _configured = True
    _configured_component = component

    # Get log level from centralized settings
    settings = Settings.get()
    level_name = settings.log_level
    level = getattr(logging, level_name, level)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create filters - will attach to handlers, not root logger
    # (filters on root logger don't always apply to all handlers consistently)
    credential_filter = _CredentialFilter()
    environment_filter = _EnvironmentFilter(settings.environment_name)

    # Reduce verbosity of noisy dependencies (always, regardless of environment)
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)
    logging.getLogger("azure.monitor").setLevel(logging.WARNING)
    logging.getLogger("opentelemetry").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("msal").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)

    # MCP/SSE loggers - INFO to reduce verbosity while still seeing key events
    logging.getLogger("sse_starlette.sse").setLevel(logging.INFO)
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    log_file_path = None

    if not settings.is_deployed:
        # Local: console + file logging
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(credential_filter)
        console_handler.addFilter(environment_filter)
        root_logger.addHandler(console_handler)

        # File handler - create logs directory if needed
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file_path = LOGS_DIR / f"{component}.log"

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(credential_filter)
        file_handler.addFilter(environment_filter)
        root_logger.addHandler(file_handler)

        _configured_log_file = str(log_file_path)
        logging.getLogger(__name__).info("Logging to %s", log_file_path)
    # Production: Application Insights only (no files, no console)
    elif connection_string := Settings.get().app_insights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
            from opentelemetry.sdk.resources import Resource

            from azurerbac import __version__

            # Create a resource with custom attributes to identify the component
            # service.name -> cloud_RoleName in App Insights
            # service.version -> application_Version in App Insights
            # service.instance.id -> cloud_RoleInstance in App Insights
            # Note: deployment.environment works for traces/spans but NOT for logs
            # Logs get environment via _EnvironmentFilter attached to handlers
            env_name = settings.environment_name
            resource = Resource.create(
                {
                    # azurerbac-ux or azurerbac-worker
                    "service.name": f"azurerbac-{component}",
                    "service.version": __version__,
                    "service.instance.id": component,  # ux or worker
                    "deployment.environment": env_name,  # for traces/spans only
                }
            )

            # Configure Azure Monitor with logging enabled
            # This sets up the OpenTelemetry logging handler automatically
            configure_azure_monitor(
                connection_string=connection_string,
                enable_live_metrics=True,
                logger_name="",  # Capture all loggers
                resource=resource,
                instrumentation_options={
                    "azure_sdk": {"enabled": True},
                    "flask": {"enabled": False},
                    "django": {"enabled": False},
                    "fastapi": {"enabled": True},
                    "psycopg2": {"enabled": True},
                    "requests": {"enabled": True},
                    "urllib": {"enabled": True},
                    "urllib3": {"enabled": True},
                },
            )

            # Enable logging instrumentation to inject trace context (operation_id)
            # into log records. This correlates logs with requests in App Insights.
            LoggingInstrumentor().instrument(set_logging_format=False)

            # Attach filters to all handlers (including those added by Azure Monitor)
            for handler in root_logger.handlers:
                handler.addFilter(credential_filter)
                handler.addFilter(environment_filter)

            logging.getLogger(__name__).info(
                "Application Insights configured - component: %s, version: %s",
                component,
                __version__,
            )
        except ImportError:
            _add_console_handler(
                root_logger, level, formatter, credential_filter, environment_filter
            )
            logging.getLogger(__name__).warning(
                "azure-monitor-opentelemetry not installed, using console logging"
            )
        except Exception as e:
            _add_console_handler(
                root_logger, level, formatter, credential_filter, environment_filter
            )
            logging.getLogger(__name__).warning(
                "Failed to configure App Insights: %s, using console logging",
                e,
            )
    else:
        # No connection string - console only
        _add_console_handler(root_logger, level, formatter, credential_filter, environment_filter)

    return _configured_log_file


def _add_console_handler(
    logger: logging.Logger,
    level: int,
    formatter: logging.Formatter,
    *filters: logging.Filter,
) -> None:
    """Add a console handler with the given formatter and filters."""
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    for f in filters:
        handler.addFilter(f)
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)
