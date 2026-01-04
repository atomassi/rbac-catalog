"""Thread-safe singleton pattern for lazy initialization.

Usage examples:

    # Pattern 1: Module-level lazy singleton with class
    _colbert_index = ThreadSafeSingleton(ColBERTIndex)

    def get_colbert_index() -> ColBERTIndex:
        return _colbert_index.get()

    # Pattern 2: With factory function (for complex initialization)
    def _load_settings():
        return Settings.load_from_env()

    _settings = ThreadSafeSingleton(factory=_load_settings)

    def get_settings() -> Settings:
        return _settings.get()
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class ThreadSafeSingleton[T]:
    """Thread-safe lazy singleton container with double-check locking.

    Requires Python 3.12+ (uses PEP 695 type parameter syntax).

    This replaces the common pattern of:
        _instance = None
        _lock = threading.Lock()

        def get_instance():
            global _instance
            if _instance is None:
                with _lock:
                    if _instance is None:
                        _instance = SomeClass()
            return _instance

    With the cleaner:
        _instance = ThreadSafeSingleton(SomeClass)

        def get_instance():
            return _instance.get()

    If initialization fails, the exception is logged and re-raised.
    Subsequent calls will retry initialization.
    """

    __slots__ = ("_factory", "_instance", "_lock")

    def __init__(
        self,
        cls: type[T] | None = None,
        *,
        factory: Callable[[], T] | None = None,
    ) -> None:
        """Initialize the singleton container.

        Args:
            cls: The class to instantiate (called with no arguments).
            factory: Alternative factory function for complex initialization.
                    Takes precedence over cls if both provided.

        Raises:
            ValueError: If neither cls nor factory is provided.
        """
        if factory is not None:
            self._factory: Callable[[], T] = factory
        elif cls is not None:
            self._factory = cls
        else:
            msg = "Either cls or factory must be provided"
            raise ValueError(msg)

        self._instance: T | None = None
        self._lock = threading.Lock()

    def get(self) -> T:
        """Get the singleton instance, creating it if necessary.

        Thread-safe using double-check locking pattern.

        Returns:
            The singleton instance.

        Raises:
            Exception: If factory/class initialization fails.
        """
        # Fast path: already initialized
        if self._instance is not None:
            return self._instance

        # Slow path: acquire lock and initialize
        with self._lock:
            # Double-check after acquiring lock
            if self._instance is not None:
                return self._instance

            # Initialize
            try:
                self._instance = self._factory()
                logger.debug("Singleton initialized: %s", type(self._instance).__name__)
            except Exception:
                logger.exception("Singleton initialization failed")
                raise

            return self._instance

    def reset(self) -> None:
        """Reset the singleton instance (useful for testing).

        Thread-safe. After reset, the next call to get() will
        create a new instance.
        """
        with self._lock:
            self._instance = None

    @property
    def is_initialized(self) -> bool:
        """Check if the singleton has been initialized."""
        return self._instance is not None
