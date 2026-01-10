"""File watcher for cache updates using watchdog.

Provides instant notification when the cache file is updated by the worker,
eliminating the need for polling-based detection.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Final

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from azurerbac.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

logger = logging.getLogger(__name__)

# Debounce interval to avoid multiple reloads from rapid file changes
_DEBOUNCE_SECONDS: Final[float] = 0.5


class _CacheFileHandler(FileSystemEventHandler):
    """Handler for cache file modification events."""

    def __init__(
        self,
        cache_filename: str,
        on_change: Callable[[], None],
    ) -> None:
        """Initialize the handler.

        Args:
            cache_filename: Name of the cache file to watch (e.g., "app_cache.msgpack")
            on_change: Callback to invoke when cache file is modified
        """
        super().__init__()
        self._cache_filename = cache_filename
        self._on_change = on_change
        self._last_trigger = 0.0
        self._lock = threading.Lock()

    def _is_target_file(self, event: FileSystemEvent) -> bool:
        """Check if this event is for our target cache file."""
        if event.is_directory:
            return False
        src_path_raw = event.src_path
        if isinstance(src_path_raw, bytes):
            src_path_raw = src_path_raw.decode("utf-8")
        return Path(src_path_raw).name == self._cache_filename

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification events."""
        if not self._is_target_file(event):
            return

        # Debounce rapid changes (atomic rename can trigger multiple events)
        with self._lock:
            now = time.time()
            if now - self._last_trigger < _DEBOUNCE_SECONDS:
                logger.debug("Watcher: debounced duplicate event within %.1fs", _DEBOUNCE_SECONDS)
                return
            self._last_trigger = now

        logger.info("Watcher: cache file modified, triggering reload")
        try:
            self._on_change()
        except Exception as e:
            logger.exception("Watcher: error in change callback: %s", e)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation events (new cache file from worker)."""
        self.on_modified(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Handle file deletion events (ignored - app keeps using in-memory cache)."""
        if not self._is_target_file(event):
            return
        logger.debug(
            "Watcher: cache file deleted, ignoring (app will continue using in-memory cache)"
        )


class CacheFileWatcher:
    """Watcher for cache file changes using watchdog.

    Uses watchdog for efficient OS-level file monitoring.
    Thread-safe and designed to work alongside async code.

    Use the module-level `cache_watcher` singleton instance.
    """

    def __init__(self) -> None:
        """Initialize the watcher."""
        self._observer: BaseObserver | None = None
        self._started = False
        self._lock = threading.Lock()

    def start(
        self,
        cache_dir: Path,
        cache_filename: str,
        on_change: Callable[[], None],
    ) -> bool:
        """Start watching the cache directory.

        Args:
            cache_dir: Directory containing the cache file
            cache_filename: Name of the cache file to watch
            on_change: Sync callback to invoke when cache file changes.
                       This runs in the watchdog thread, so keep it lightweight
                       (e.g., set a flag, schedule async task).

        Returns:
            True if watcher started successfully, False otherwise
        """
        with self._lock:
            if self._started:
                logger.debug("Watcher: already running, skipping start")
                return True

            try:
                # Ensure directory exists
                cache_dir.mkdir(parents=True, exist_ok=True)

                self._observer = Observer()
                handler = _CacheFileHandler(cache_filename, on_change)
                self._observer.schedule(handler, str(cache_dir), recursive=False)
                self._observer.start()
                self._started = True
                logger.info("Watcher: started monitoring %s/%s", cache_dir, cache_filename)
                return True
            except Exception as e:
                logger.exception("Watcher: failed to start: %s", e)
                return False

    def stop(self) -> None:
        """Stop the file watcher."""
        with self._lock:
            if not self._started or self._observer is None:
                return

            try:
                self._observer.stop()
                self._observer.join(timeout=5.0)
                logger.info("Watcher: stopped")
            except Exception as e:
                logger.exception("Watcher: error during stop: %s", e)
            finally:
                self._observer = None
                self._started = False

    @property
    def is_running(self) -> bool:
        """Check if the watcher is currently running."""
        with self._lock:
            return self._started and self._observer is not None and self._observer.is_alive()


# Singleton instance using ThreadSafeSingleton
_watcher_singleton: ThreadSafeSingleton[CacheFileWatcher] = ThreadSafeSingleton(CacheFileWatcher)


def get_cache_watcher() -> CacheFileWatcher:
    """Get the singleton cache file watcher instance."""
    return _watcher_singleton.get()
