"""Timing utilities for metrics."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Self

from azurerbac.telemetry.metrics import (
    track_db_fallback,
    track_db_query,
    track_db_query_error,
)


class BaseTimer(ABC):
    """Base class for timing context managers."""

    __slots__ = ("_start_time",)

    def __init__(self) -> None:
        self._start_time: float = 0

    def __enter__(self) -> Self:
        self._start_time = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        self._on_exit(time.perf_counter() - self._start_time, exc_type)

    async def __aenter__(self) -> Self:
        self._start_time = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        self._on_exit(time.perf_counter() - self._start_time, exc_type)

    @abstractmethod
    def _on_exit(self, elapsed: float, exc_type: type[BaseException] | None) -> None:
        pass


class TimedDbQuery(BaseTimer):
    """Timer for database queries with metrics tracking."""

    __slots__ = ("fallback_type", "query_name", "rows")

    def __init__(self, query_name: str, *, fallback_type: str | None = None) -> None:
        super().__init__()
        self.query_name = query_name
        self.rows: int | None = None
        self.fallback_type = fallback_type

    def _on_exit(self, elapsed: float, exc_type: type[BaseException] | None) -> None:
        if exc_type is not None:
            track_db_query_error(self.query_name, elapsed, exc_type.__name__)
            return

        track_db_query(self.query_name, elapsed, self.rows)
        if self.fallback_type:
            track_db_fallback(self.fallback_type, "cache_miss", self.query_name)
