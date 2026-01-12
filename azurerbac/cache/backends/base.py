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
    """Abstract base class for cache storage backends."""

    @abstractmethod
    async def save(self, data: CacheData) -> bool:
        """Save cache data to storage."""

    @abstractmethod
    async def load(self) -> CacheData | None:
        """Load cache data from storage."""

    @abstractmethod
    async def delete(self) -> None:
        """Delete cached data from storage."""

    def exists(self) -> bool:
        """Check if cached data exists."""
        return self.get_version() is not None

    @abstractmethod
    def get_version(self) -> str | None:
        """Get version identifier for change detection."""

    def get_mtime(self) -> float | None:
        """Get modification time as Unix timestamp."""
        version = self.get_version()
        if version is None:
            return None
        try:
            return float(version)
        except (ValueError, TypeError):
            return None

    def subscribe(self, callback: Callable[[], None]) -> bool:
        """Subscribe to cache change notifications. Returns False if unsupported."""
        return False  # Default: not supported

    def unsubscribe(self) -> None:  # noqa: B027
        """Stop receiving change notifications."""

    @property
    def is_subscribed(self) -> bool:
        """Check if subscribed to changes."""
        return False

    async def close(self) -> None:  # noqa: B027
        """Close connections and release resources."""


def create_backend() -> CacheBackend:
    """Create a backend instance based on settings."""
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
