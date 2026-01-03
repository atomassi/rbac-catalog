"""Application Insights and file logging configuration using OpenTelemetry."""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import Final, Self

from azurerbac.settings import Settings, is_running_in_azure

# Log directory for local file logging
LOGS_DIR: Final = Path(__file__).parent.parent.parent / "logs"

# Module logger for internal errors
_logger = logging.getLogger(__name__)

_configured = False
_configured_log_file: str | None = None
_configured_component: str | None = None

# Patterns that indicate sensitive data - redact these from logs
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
    """Filter to redact sensitive information from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact sensitive patterns from log message.

        Notes:
            We inspect the rendered message (record.getMessage()) so secrets
            provided via logging args are also caught.
        """
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - defensive; logging should never break
            return True

        if isinstance(rendered, str):
            rendered_lower = rendered.lower()
            for pattern in _SENSITIVE_PATTERNS:
                if pattern.lower() in rendered_lower:
                    record.msg = "[REDACTED - contains sensitive data]"
                    record.args = ()
                    break
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

    # Add credential filter to prevent sensitive data leakage
    root_logger.addFilter(_CredentialFilter())

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

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    log_file_path = None

    if not is_running_in_azure():
        # Local: console + file logging
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File handler - create logs directory if needed
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_file_path = LOGS_DIR / f"{component}.log"

        file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
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
            resource = Resource.create(
                {
                    # azurerbac-ux or azurerbac-worker
                    "service.name": f"azurerbac-{component}",
                    "service.version": __version__,
                    "service.instance.id": component,  # ux or worker
                    "deployment.environment": "production",
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

            logging.getLogger(__name__).info(
                "Application Insights configured - component: %s, version: %s",
                component,
                __version__,
            )
        except ImportError:
            # Fallback to console if azure-monitor-opentelemetry not available
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

            logging.getLogger(__name__).warning(
                "azure-monitor-opentelemetry not installed, using console logging"
            )
        except Exception as e:
            # Fallback to console on any error
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            root_logger.addHandler(console_handler)

            logging.getLogger(__name__).warning(
                "Failed to configure App Insights: %s, using console logging",
                e,
            )
    else:
        # No connection string - console only
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    return _configured_log_file


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class WorkerOperationContext:
    """Context manager that creates a trace span for worker operations.

    This generates a unique operation_Id that correlates all logs within
    the context block. Use this for background jobs (scans, scheduled tasks)
    that don't have an incoming HTTP request.

    Usage:
        with WorkerOperationContext("role-scan"):
            logger.info("Starting scan...")  # All logs share same operation_Id
            # ... do work ...
            logger.info("Scan complete")

    In App Insights, query with:
        traces | where operation_Name == "role-scan" | order by timestamp

    Args:
        operation_name: Name for this operation (appears in operation_Name)
    """

    def __init__(self, operation_name: str) -> None:
        self.operation_name = operation_name
        self._span = None
        self._token = None

    def __enter__(self) -> Self:
        try:
            from opentelemetry import context, trace

            tracer = trace.get_tracer(__name__)
            self._span = tracer.start_span(self.operation_name)
            span_context = trace.set_span_in_context(self._span)
            self._token = context.attach(span_context)
        except ImportError:
            # OpenTelemetry not available - continue without tracing
            pass
        except Exception:
            # Any other error - continue without tracing
            _logger.warning("Failed to start tracing span", exc_info=True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        if self._span is not None:
            try:
                from opentelemetry import context
                from opentelemetry.trace.status import Status, StatusCode

                if exc_type is not None:
                    # Record exception in span
                    if exc_val is not None:
                        self._span.record_exception(exc_val)
                        description = str(exc_val)
                    else:
                        description = exc_type.__name__
                    self._span.set_status(Status(StatusCode.ERROR, description))
                else:
                    self._span.set_status(Status(StatusCode.OK))
                self._span.end()

                if self._token is not None:
                    context.detach(self._token)
            except Exception:
                _logger.warning("Failed to end tracing span", exc_info=True)
        return False  # Don't suppress exceptions
