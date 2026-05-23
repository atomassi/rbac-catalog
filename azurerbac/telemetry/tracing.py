"""OpenTelemetry tracing context managers for worker operations."""

import logging
from types import TracebackType
from typing import Self

_logger = logging.getLogger(__name__)


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
    ) -> bool:
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
