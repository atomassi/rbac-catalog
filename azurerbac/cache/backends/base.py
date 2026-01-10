"""Abstract base class for cache storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from azurerbac.cache.models import CacheData


class CacheBackendType(Enum):
    """Available cache backend implementations."""

    FILE = auto()  # Local file with watchdog (default)
    # Future: REDIS = auto(), MEMCACHED = auto()


class CacheBackend(ABC):
    """Abstract base class for cache storage backends.

    Current implementation:
        - FileCacheBackend: Local file with watchdog for change detection.
          Suitable for single-VM deployments where all workers share a filesystem.

    Future options (if disk I/O becomes a bottleneck or horizontal scaling needed):
        - RedisCacheBackend: Redis with pub/sub for multi-instance invalidation.
        - MemcachedBackend: Simple key-value with no pub/sub.

    All I/O methods are async to support non-blocking operations.

    Method groups:
        Core CRUD: save(), load(), delete(), exists()
        Versioning: get_version(), get_mtime() - for change detection
        Notifications: subscribe(), unsubscribe(), is_subscribed - optional pub/sub
        Lifecycle: close() - cleanup connections/resources
    """

    # -------------------------------------------------------------------------
    # Core CRUD operations
    # -------------------------------------------------------------------------

    @abstractmethod
    async def save(self, data: CacheData) -> bool:
        """Save cache data to storage.

        Args:
            data: The CacheData to persist.

        Returns:
            True if successful, False otherwise.
        """

    @abstractmethod
    async def load(self) -> CacheData | None:
        """Load cache data from storage.

        Returns:
            The loaded CacheData, or None if not found or error.
        """

    @abstractmethod
    async def delete(self) -> None:
        """Delete the cached data from storage."""

    def exists(self) -> bool:
        """Check if cached data exists.

        Default implementation uses get_version(). Override for efficiency.
        """
        return self.get_version() is not None

    # -------------------------------------------------------------------------
    # Versioning (for change detection)
    # -------------------------------------------------------------------------

    @abstractmethod
    def get_version(self) -> str | None:
        """Get a version identifier for the cached data.

        Used for change detection. The version changes when data is updated.

        Implementation examples:
            - File: mtime as string
            - Redis: OBJECT ENCODING or custom version key
            - Memcached: CAS token

        Returns:
            Version string, or None if cache doesn't exist.
        """

    def get_mtime(self) -> float | None:
        """Get modification time as Unix timestamp, or None if unavailable."""
        version = self.get_version()
        if version is None:
            return None
        try:
            return float(version)
        except (ValueError, TypeError):
            return None

    # -------------------------------------------------------------------------
    # Change notifications (optional - for pub/sub backends)
    # -------------------------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> bool:
        """Subscribe to cache change notifications.

        Optional. Backends without pub/sub (e.g., Memcached) return False.
        Callers should fall back to polling get_version() if False.

        Args:
            callback: Function to call when cache is updated.
                     Should be lightweight (just set a flag).

        Returns:
            True if subscription started successfully, False if not supported.
        """
        return False  # Default: not supported

    def unsubscribe(self) -> None:  # noqa: B027
        """Stop receiving cache change notifications.

        No-op if subscribe() returned False or wasn't called.
        """

    @property
    def is_subscribed(self) -> bool:
        """Check if currently subscribed to changes."""
        return False  # Default: not subscribed

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def close(self) -> None:  # noqa: B027
        """Close connections and release resources.

        Called during application shutdown. Override for backends with
        connections (Redis, Memcached).
        """


def create_backend() -> CacheBackend:
    """Create a backend instance based on settings.

    Called by CacheService to instantiate the backend.
    Do not call directly - use get_cache_service().backend instead.
    """
    from azurerbac.settings import get_settings

    settings = get_settings()
    backend_type = CacheBackendType[settings.cache_backend.upper()]

    match backend_type:
        case CacheBackendType.FILE:
            from azurerbac.cache.backends.file import FileCacheBackend

            return FileCacheBackend()
        # Future: case CacheBackendType.REDIS: ...
        case _:
            msg = f"Unknown backend type: {backend_type}"
            raise ValueError(msg)
