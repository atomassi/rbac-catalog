"""Thread-safe singleton with double-check locking."""

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ThreadSafeSingleton[T]:
    """Lazy singleton container (Python 3.12+)."""

    __slots__ = ("_factory", "_instance", "_lock")

    def __init__(
        self, cls: type[T] | None = None, *, factory: Callable[[], T] | None = None
    ) -> None:
        if factory is not None:
            self._factory: Callable[[], T] = factory
        elif cls is not None:
            self._factory = cls
        else:
            raise ValueError("Either cls or factory must be provided")
        self._instance: T | None = None
        self._lock = threading.Lock()

    def get(self) -> T:
        """Get or create the singleton instance."""
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                try:
                    self._instance = self._factory()
                    logger.debug("Singleton initialized: %s", type(self._instance).__name__)
                except Exception:
                    logger.exception("Singleton initialization failed")
                    raise
        return self._instance

    def reset(self) -> None:
        """Reset the instance (thread-safe)."""
        with self._lock:
            self._instance = None

    @property
    def is_initialized(self) -> bool:
        return self._instance is not None
