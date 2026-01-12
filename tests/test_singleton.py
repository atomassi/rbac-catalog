"""Tests for the singleton module."""

from __future__ import annotations

import threading
import time

import pytest

from azurerbac.core.singleton import ThreadSafeSingleton


class TestThreadSafeSingleton:
    """Tests for ThreadSafeSingleton class."""

    def test_basic_singleton_returns_same_instance(self):
        """Test that get() returns the same instance every time."""

        class Counter:
            def __init__(self):
                self.count = 0

        singleton = ThreadSafeSingleton(Counter)
        c1 = singleton.get()
        c2 = singleton.get()

        c1.count = 42
        assert c2.count == 42
        assert c1 is c2

    def test_singleton_with_factory(self):
        """Test singleton with custom factory function."""
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return {"value": call_count}

        singleton = ThreadSafeSingleton(factory=factory)
        result1 = singleton.get()
        result2 = singleton.get()

        assert result1 is result2
        assert call_count == 1  # Factory called only once
        assert result1["value"] == 1

    def test_factory_exception_raises_original(self):
        """Test that exceptions in factory are logged and re-raised."""

        def failing_factory():
            msg = "Initialization failed"
            raise ValueError(msg)

        singleton = ThreadSafeSingleton(factory=failing_factory)

        with pytest.raises(ValueError, match="Initialization failed"):
            singleton.get()

    def test_failed_init_allows_retry(self):
        """Test that failed initialization allows retry on subsequent calls."""
        call_count = 0

        def failing_then_succeeding_factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Transient failure")
            return "success"

        singleton = ThreadSafeSingleton(factory=failing_then_succeeding_factory)

        with pytest.raises(ValueError):
            singleton.get()
        assert call_count == 1

        # Second call should retry and succeed
        result = singleton.get()
        assert result == "success"
        assert call_count == 2

    def test_reset_clears_instance(self):
        """Test that reset() clears the cached instance."""

        class Counter:
            def __init__(self):
                self.count = 0

        singleton = ThreadSafeSingleton(Counter)

        c1 = singleton.get()
        c1.count = 100

        singleton.reset()

        c2 = singleton.get()
        assert c2.count == 0  # Fresh instance
        assert c1 is not c2

    def test_is_initialized_lifecycle(self):
        """Test is_initialized property through lifecycle."""

        class Simple:
            pass

        singleton = ThreadSafeSingleton(Simple)

        assert not singleton.is_initialized  # Before get()
        singleton.get()
        assert singleton.is_initialized  # After get()
        singleton.reset()
        assert not singleton.is_initialized  # After reset()

    def test_thread_safety(self):
        """Test that singleton is thread-safe under concurrent access."""
        creation_count = 0
        creation_lock = threading.Lock()

        class TrackedClass:
            def __init__(self):
                nonlocal creation_count
                with creation_lock:
                    creation_count += 1
                time.sleep(0.01)  # Simulate slow initialization

        singleton = ThreadSafeSingleton(TrackedClass)
        instances = []
        instance_lock = threading.Lock()

        def get_instance():
            inst = singleton.get()
            with instance_lock:
                instances.append(inst)

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert creation_count == 1  # Only one instance created
        assert all(i is instances[0] for i in instances)

    def test_requires_cls_or_factory(self):
        """Test that ValueError is raised if neither cls nor factory provided."""
        with pytest.raises(ValueError, match="Either cls or factory must be provided"):
            ThreadSafeSingleton()
