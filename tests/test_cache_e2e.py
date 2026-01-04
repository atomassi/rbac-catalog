"""End-to-end tests for cache refresh scenarios.

These tests verify complete cache workflows across all sources:
1. Worker process refresh (rebuild_cache -> save to disk -> web reload)
2. Periodic web app refresh (precompute every hour)
3. Web startup flow (load from disk OR rebuild from DB)
4. Cache version compatibility (v4 format with metadata)
5. Race condition handling (concurrent reloads, rebuilds)
6. Data consistency guarantees

Each test simulates a real-world scenario with actual data flow.
"""

import tempfile
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from azurerbac.cache import (
    CacheData,
    CacheMetadata,
    app_cache,
    clear_computed_caches,
    compute_operations_hash,
    compute_roles_hash,
    delete_cache_file,
    load_cache_from_disk,
    precompute_all_caches,
    save_cache_to_disk,
)
from azurerbac.cache.models import CACHE_VERSION


@pytest.fixture
def sample_change_events():
    """Sample change events for testing."""
    return [
        {
            "id": 1,
            "role_id": "reader-role-id",
            "role_name": "Reader",
            "event_type": "created",
            "scan_timestamp": datetime(2024, 1, 1, 12, 0, 0),
            "azure_updated_on": datetime(2024, 1, 1, 10, 0, 0),
            "summary": "Role created",
            "diff_json": None,
            "role_json": {
                "id": "reader-role-id",
                "properties": {"roleName": "Reader", "type": "BuiltInRole"},
            },
        },
        {
            "id": 2,
            "role_id": "storage-data-reader-id",
            "role_name": "Storage Data Reader",
            "event_type": "updated",
            "scan_timestamp": datetime(2024, 1, 15, 12, 0, 0),
            "azure_updated_on": datetime(2024, 1, 15, 10, 0, 0),
            "summary": "Permissions updated",
            "diff_json": {"changes": [{"field": "permissions", "old": "X", "new": "Y"}]},
            "role_json": {
                "id": "storage-data-reader-id",
                "properties": {"roleName": "Storage Data Reader", "type": "BuiltInRole"},
            },
        },
    ]


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture(autouse=True)
def clean_cache():
    """Clean up caches before and after each test."""
    # Clear before test
    app_cache._cache = CacheData()
    app_cache._role_pages.clear()
    app_cache._misc_cache.clear()
    app_cache._preloaded = False
    app_cache._loaded_cache_mtime = None
    app_cache._last_cache_check = 0
    clear_computed_caches()
    yield
    # Clear after test
    app_cache._cache = CacheData()
    app_cache._role_pages.clear()
    app_cache._misc_cache.clear()
    app_cache._preloaded = False
    app_cache._loaded_cache_mtime = None
    app_cache._last_cache_check = 0
    clear_computed_caches()


def build_roles_by_id(roles: list[dict]) -> dict:
    """Helper to build roles_by_id index from role list."""
    return {
        r["name"]: {
            "role_id": r["name"],
            "role_name": r["properties"]["roleName"],
            "role_type": r["properties"].get("type", "BuiltInRole"),
            "status": "active",
            "updated_on": None,
            "last_seen_at": None,
            "role_json": r,
        }
        for r in roles
    }


def build_complete_cache(
    roles: list[dict],
    operations: list[dict],
    change_events: list[dict] | None = None,
) -> CacheData:
    """Helper to build a complete CacheData with all computed fields."""
    roles_by_id = build_roles_by_id(roles)
    metadata = CacheMetadata(
        roles_count=len(roles),
        operations_count=len(operations),
        roles_hash=compute_roles_hash(roles),
        operations_hash=compute_operations_hash(operations),
    )

    cache_data = precompute_all_caches(
        roles,
        operations,
        metadata=metadata,
        roles_by_id=roles_by_id,
        all_change_events=change_events or [],
        swap_in_memory=False,
    )
    return cache_data


# =============================================================================
# Worker Refresh Flow Tests
# =============================================================================


class TestWorkerRefreshFlow:
    """Tests for worker process cache refresh (the primary refresh mechanism)."""

    def test_worker_builds_complete_cache_and_saves_to_disk(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Worker builds complete cache with all computed fields and saves to disk."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Worker builds complete cache
            cache_data = build_complete_cache(sample_roles, sample_operations, sample_change_events)

            # Verify all fields are populated
            assert cache_data.metadata is not None
            assert cache_data.metadata.version == CACHE_VERSION
            assert len(cache_data.roles_by_id) == len(sample_roles)
            assert len(cache_data.all_operations) == len(sample_operations)
            assert len(cache_data.all_change_events) == len(sample_change_events)
            assert len(cache_data.role_coverage) == len(sample_roles)
            assert len(cache_data.role_net_permissions) == len(sample_roles)
            assert len(cache_data.operations_for_recommender) == len(sample_operations)
            assert len(cache_data.unique_providers) > 0

            # Save to disk
            result = save_cache_to_disk(cache_data)
            assert result is True

            # Verify file exists and is substantial
            cache_file = temp_cache_dir / "app_cache.msgpack"
            assert cache_file.exists()
            assert cache_file.stat().st_size > 1000  # Should be several KB

    def test_web_process_loads_precomputed_cache_from_disk(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Web process loads complete cache from disk - no recomputation needed."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Worker saves cache with precomputed data
            cache_data = build_complete_cache(sample_roles, sample_operations)
            original_coverage = dict(cache_data.role_coverage)
            save_cache_to_disk(cache_data)

            # Web process loads from disk
            loaded = load_cache_from_disk()

            assert loaded is not None
            assert loaded.metadata.version == CACHE_VERSION
            assert len(loaded.roles_by_id) == len(sample_roles)
            assert len(loaded.all_operations) == len(sample_operations)

            # Precomputed fields should be loaded directly
            assert loaded.role_coverage == original_coverage
            assert len(loaded.role_net_permissions) == len(sample_roles)

    def test_web_detects_worker_update_and_reloads(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Web process detects worker disk update and reloads cache."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Initial state - web has old cache
            old_cache = CacheData(
                metadata=CacheMetadata(roles_count=1, operations_count=1),
                all_operations=[{"name": "old-op", "is_data_action": False}],
                roles_by_id={"old-role": {"role_id": "old-role", "role_json": {}}},
            )
            app_cache.swap(old_cache)
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            # Worker saves new cache
            new_cache = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(new_cache)

            # Web detects update and reloads
            # (single call since needs_reload updates _last_cache_check)
            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                result = app_cache.reload_from_disk_if_needed()
                assert result is True

            # Verify web now has new data
            assert len(app_cache.cache.all_operations) == len(sample_operations)
            assert len(app_cache.cache.roles_by_id) == len(sample_roles)
            assert len(app_cache.cache.role_coverage) == len(sample_roles)

    def test_reload_is_atomic_no_partial_state(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Reload is atomic - readers never see partial state."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Setup initial cache
            old_ops = [{"name": "old-op", "is_data_action": False}]
            app_cache.build_from_operations(old_ops)
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            # Save new cache to disk
            new_cache = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(new_cache)

            # Capture reference before reload
            old_cache_ref = app_cache.cache
            assert len(old_cache_ref.all_operations) == 1

            # Reload
            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                app_cache.reload_from_disk_if_needed()

            # New cache is different object
            new_cache_ref = app_cache.cache
            assert new_cache_ref is not old_cache_ref
            assert len(new_cache_ref.all_operations) == len(sample_operations)

            # Old reference is unchanged (readers with old ref see consistent data)
            assert len(old_cache_ref.all_operations) == 1

    def test_worker_refresh_clears_recommender_caches_before_rebuild(
        self, sample_roles, sample_operations
    ):
        """Worker clears computed caches before rebuilding to ensure consistency."""
        # Setup with computed data
        app_cache.build_from_operations(sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )
        precompute_all_caches(sample_roles, sample_operations)

        # Verify computed data exists
        assert app_cache.get_role_coverage("reader-role-id") is not None

        # Simulate worker clear (before rebuild)
        clear_computed_caches()

        # Computed data should be cleared
        assert app_cache.get_role_coverage("reader-role-id") is None

        # Source data preserved
        assert len(app_cache.cache.all_operations) == len(sample_operations)


# =============================================================================
# Periodic Web Refresh Flow Tests
# =============================================================================


class TestPeriodicWebRefreshFlow:
    """Tests for periodic cache refresh in web process (hourly)."""

    def test_periodic_refresh_recomputes_from_current_cache_data(
        self, sample_roles, sample_operations
    ):
        """Periodic refresh recomputes using current cache data."""
        # Initial setup
        app_cache.build_from_operations(sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # First compute
        precompute_all_caches(sample_roles, sample_operations)
        first_coverage = app_cache.get_role_coverage("reader-role-id")

        # Simulate 1 hour later - periodic refresh uses data from cache
        role_jsons = app_cache.get_all_role_jsons()
        all_ops = app_cache.cache.all_operations

        # Recompute
        precompute_all_caches(role_jsons, all_ops)
        second_coverage = app_cache.get_role_coverage("reader-role-id")

        # Results should be identical
        assert first_coverage == second_coverage

    def test_periodic_refresh_updates_pattern_match_cache(self, sample_roles, sample_operations):
        """Periodic refresh updates all pattern caches."""
        app_cache.build_from_operations(sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        precompute_all_caches(sample_roles, sample_operations)

        # Pattern match cache should be populated
        assert len(app_cache.cache.pattern_match) > 0

    def test_multiple_periodic_refreshes_are_consistent(self, sample_roles, sample_operations):
        """Multiple periodic refreshes produce identical results."""
        app_cache.build_from_operations(sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        results = []
        for _ in range(5):
            precompute_all_caches(sample_roles, sample_operations)
            coverage = app_cache.get_role_coverage("reader-role-id")
            net_perms = app_cache.get_role_net_permissions("reader-role-id")
            results.append((len(coverage[0]), len(coverage[1]), net_perms))

        # All should be identical
        assert len(set(results)) == 1


# =============================================================================
# Web Startup Flow Tests
# =============================================================================


class TestWebStartupFlow:
    """Tests for web app startup cache initialization."""

    def test_startup_with_valid_disk_cache_loads_directly(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with valid disk cache loads directly - no DB query needed."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Previous run saved cache
            cache_data = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache_data)

            # Startup loads from disk
            loaded = load_cache_from_disk()

            assert loaded is not None
            assert len(loaded.roles_by_id) == len(sample_roles)
            assert len(loaded.all_operations) == len(sample_operations)
            # Computed fields are already populated
            assert len(loaded.role_coverage) == len(sample_roles)

    def test_startup_with_no_disk_cache_builds_from_scratch(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with no disk cache builds from database."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # No disk cache
            loaded = load_cache_from_disk()
            assert loaded is None

            # Build from "database" (simulated)
            app_cache.build_from_operations(sample_operations)
            roles_by_id = build_roles_by_id(sample_roles)
            app_cache._cache = CacheData(
                all_operations=sample_operations,
                roles_by_id=roles_by_id,
                ops_by_name_lower=app_cache.cache.ops_by_name_lower,
                ops_by_prefix=app_cache.cache.ops_by_prefix,
            )

            # Precompute
            precompute_all_caches(sample_roles, sample_operations)

            # Save for next startup
            save_cache_to_disk(app_cache.cache)

            # Verify data is available
            assert app_cache.get_role_coverage("reader-role-id") is not None

    def test_startup_with_stale_cache_rebuilds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with stale disk cache (wrong counts) triggers rebuild."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Save cache with 3 roles
            cache_data = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache_data)

            # Startup with different counts (simulating DB has more data)
            loaded = load_cache_from_disk()
            assert loaded is not None

            # Check if valid for different counts
            is_valid = loaded.metadata.is_valid_for(100, 1000)  # Different counts
            assert is_valid is False  # Should be invalid

    def test_startup_with_old_version_cache_rebuilds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with old cache version triggers rebuild."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Save cache with old version
            cache_data = build_complete_cache(sample_roles, sample_operations)
            # Manually set old version
            old_metadata = CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            )
            old_metadata.version = "v1"  # Old version
            cache_data = CacheData(
                metadata=old_metadata,
                roles_by_id=cache_data.roles_by_id,
                all_operations=cache_data.all_operations,
            )
            save_cache_to_disk(cache_data)

            # Startup tries to reload
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                result = app_cache.reload_from_disk_if_needed()

            # Should fail due to version mismatch
            assert result is False


# =============================================================================
# Cache Version and Format Tests
# =============================================================================


class TestCacheVersionFormat:
    """Tests for cache version handling and format compatibility."""

    def test_cache_has_correct_version(self, sample_roles, sample_operations):
        """Cache metadata has correct version."""
        cache_data = build_complete_cache(sample_roles, sample_operations)
        assert cache_data.metadata.version == CACHE_VERSION

    def test_cache_metadata_includes_hashes(self, sample_roles, sample_operations):
        """Cache metadata includes content hashes for validation."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        assert cache_data.metadata.roles_hash is not None
        assert cache_data.metadata.operations_hash is not None
        assert len(cache_data.metadata.roles_hash) > 0
        assert len(cache_data.metadata.operations_hash) > 0

    def test_cache_metadata_validates_counts(self, sample_roles, sample_operations):
        """Cache metadata can validate role/operation counts."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        # Valid for same counts
        assert cache_data.metadata.is_valid_for(len(sample_roles), len(sample_operations))

        # Invalid for different counts
        assert not cache_data.metadata.is_valid_for(100, 1000)

    def test_cache_includes_all_computed_fields(self, sample_roles, sample_operations):
        """Cache includes all computed fields for direct load."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        # Source data
        assert len(cache_data.all_operations) == len(sample_operations)
        assert len(cache_data.roles_by_id) == len(sample_roles)

        # Derived data
        assert len(cache_data.operations_for_recommender) == len(sample_operations)
        assert len(cache_data.unique_providers) > 0

        # Computed data
        assert len(cache_data.role_coverage) == len(sample_roles)
        assert len(cache_data.role_net_permissions) == len(sample_roles)
        assert len(cache_data.pattern_match) > 0

        # Indexes
        assert len(cache_data.ops_by_name_lower) == len(sample_operations)


# =============================================================================
# Race Condition and Thread Safety Tests
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety and race condition handling."""

    def test_concurrent_reloads_only_one_proceeds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Only one concurrent reload proceeds, others skip."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Save cache
            cache_data = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache_data)

            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            results = []
            errors = []

            def attempt_reload(thread_id):
                try:
                    with patch(
                        "azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0
                    ):
                        result = app_cache.reload_from_disk_if_needed()
                        results.append((thread_id, result))
                except Exception as e:
                    errors.append((thread_id, str(e)))

            # Start multiple threads
            threads = [threading.Thread(target=attempt_reload, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0
            # At most one should succeed (first one to acquire lock)
            successful = [r for r in results if r[1] is True]
            assert len(successful) <= 1

    def test_readers_see_consistent_data_during_swap(self, sample_roles, sample_operations):
        """Readers always see consistent data even during cache swap."""
        # Setup initial cache
        app_cache.build_from_operations(sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )
        precompute_all_caches(sample_roles, sample_operations)

        read_results = []
        errors = []

        def reader(reader_id):
            """Read multiple times and verify consistency."""
            try:
                for _ in range(100):
                    cache = app_cache.cache  # Capture reference
                    ops_count = len(cache.all_operations)
                    roles_count = len(cache.roles_by_id)
                    # These should be consistent within single read
                    read_results.append((reader_id, ops_count, roles_count))
            except Exception as e:
                errors.append((reader_id, str(e)))

        def writer():
            """Swap cache multiple times."""
            for _ in range(50):
                new_cache = CacheData(
                    all_operations=sample_operations,
                    roles_by_id=roles_by_id,
                )
                app_cache.swap(new_cache)

        # Run concurrent readers and writer
        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        threads.append(threading.Thread(target=writer))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        # All reads should show consistent counts (ops and roles match expected)
        for _, ops_count, roles_count in read_results:
            assert ops_count == len(sample_operations) or ops_count == 0
            assert roles_count == len(sample_roles) or roles_count == 0


# =============================================================================
# Data Consistency Tests
# =============================================================================


class TestDataConsistency:
    """Tests for data consistency across cache operations."""

    def test_role_coverage_matches_role_data(self, sample_roles, sample_operations):
        """Role coverage is computed for all roles in cache."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        # Every role should have coverage
        for role_id in cache_data.roles_by_id:
            assert role_id in cache_data.role_coverage

    def test_role_net_permissions_matches_role_data(self, sample_roles, sample_operations):
        """Role net permissions is computed for all roles in cache."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        # Every role should have net permissions
        for role_id in cache_data.roles_by_id:
            assert role_id in cache_data.role_net_permissions

    def test_operations_for_recommender_matches_all_operations(
        self, sample_roles, sample_operations
    ):
        """operations_for_recommender has same count as all_operations."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        assert len(cache_data.operations_for_recommender) == len(sample_operations)

    def test_unique_providers_extracted_correctly(self, sample_roles, sample_operations):
        """unique_providers contains all providers from operations."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        expected_providers = {
            op["provider_display_name"]
            for op in sample_operations
            if op.get("provider_display_name")
        }

        assert set(cache_data.unique_providers) == expected_providers

    def test_no_role_data_pollution_between_roles(self, sample_operations):
        """One role's computed data doesn't pollute another role's cache."""
        roles = [
            {
                "name": "reader",
                "properties": {
                    "roleName": "Reader",
                    "type": "BuiltInRole",
                    "permissions": [
                        {
                            "actions": ["*/read"],
                            "notActions": [],
                            "dataActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            },
            {
                "name": "writer",
                "properties": {
                    "roleName": "Writer",
                    "type": "BuiltInRole",
                    "permissions": [
                        {
                            "actions": ["*/write"],
                            "notActions": [],
                            "dataActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            },
        ]

        cache_data = build_complete_cache(roles, sample_operations)

        reader_coverage = cache_data.role_coverage["reader"]
        writer_coverage = cache_data.role_coverage["writer"]

        # Reader only has read ops
        reader_ops = reader_coverage[0]
        for op in reader_ops:
            assert "read" in op.lower()

        # Writer only has write ops
        writer_ops = writer_coverage[0]
        for op in writer_ops:
            assert "write" in op.lower()

        # No overlap
        assert reader_ops.isdisjoint(writer_ops)

    def test_change_events_preserved_through_cache_cycle(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Change events are preserved through save/load cycle."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Build and save
            cache_data = build_complete_cache(sample_roles, sample_operations, sample_change_events)
            save_cache_to_disk(cache_data)

            # Load
            loaded = load_cache_from_disk()

            assert len(loaded.all_change_events) == len(sample_change_events)
            for i, event in enumerate(loaded.all_change_events):
                assert event["role_id"] == sample_change_events[i]["role_id"]
                assert event["event_type"] == sample_change_events[i]["event_type"]

    def test_change_events_include_role_json(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Change events include role_json for created/updated events.

        This is required for the Change History to display the full JSON
        for 'created' events, especially important for deleted roles where
        the current role_json is NULL.
        """
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Build and save
            cache_data = build_complete_cache(sample_roles, sample_operations, sample_change_events)
            save_cache_to_disk(cache_data)

            # Load
            loaded = load_cache_from_disk()

            # Verify role_json is preserved
            for i, event in enumerate(loaded.all_change_events):
                original = sample_change_events[i]
                assert "role_json" in event, f"Event {i} missing role_json"
                if original.get("role_json"):
                    assert event["role_json"] == original["role_json"]
                    assert event["role_json"]["properties"]["roleName"]


# =============================================================================
# Invalidation Flow Tests
# =============================================================================


class TestInvalidationFlow:
    """Tests for cache invalidation scenarios."""

    def test_delete_cache_file_removes_disk_file(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """delete_cache_file removes the disk cache file."""
        with (patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir),):
            # Save cache
            cache_data = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache_data)

            cache_file = temp_cache_dir / "app_cache.msgpack"
            assert cache_file.exists()

            # Delete cache file
            delete_cache_file()
            assert not cache_file.exists()

    def test_invalidate_all_clears_everything(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """invalidate_all clears all caches including memory and disk."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Setup cache
            cache_data = build_complete_cache(sample_roles, sample_operations)
            app_cache.swap(cache_data)
            app_cache._role_pages["page1"] = [{"test": True}]
            app_cache._misc_cache["key1"] = "value1"
            save_cache_to_disk(app_cache.cache)

            # Verify everything exists
            assert len(app_cache.cache.all_operations) > 0
            assert len(app_cache._role_pages) > 0
            assert len(app_cache._misc_cache) > 0
            assert (temp_cache_dir / "app_cache.msgpack").exists()

            # Invalidate all
            app_cache.invalidate_all()

            # Everything should be cleared
            assert len(app_cache.cache.all_operations) == 0
            assert len(app_cache.cache.roles_by_id) == 0
            assert len(app_cache._role_pages) == 0
            assert len(app_cache._misc_cache) == 0
            assert not (temp_cache_dir / "app_cache.msgpack").exists()

    def test_swap_clears_misc_cache(self, sample_operations):
        """Atomic swap clears misc_cache (dynamic lookups like roles_allowing_op)."""
        # Populate misc cache
        app_cache._misc_cache["roles_allowing_op:test"] = ["role1", "role2"]
        assert app_cache.get("roles_allowing_op:test") is not None

        # Swap with new cache
        new_cache = CacheData(all_operations=sample_operations)
        app_cache.swap(new_cache)

        # Misc cache should be cleared
        assert app_cache.get("roles_allowing_op:test") is None


# =============================================================================
# Full Lifecycle Integration Tests
# =============================================================================


class TestFullLifecycleE2E:
    """End-to-end tests for complete cache lifecycle."""

    def test_complete_lifecycle_startup_worker_reload_refresh(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Test complete lifecycle: startup -> worker update -> web reload -> periodic refresh."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # === PHASE 1: Cold Start ===
            # No disk cache, web builds from DB
            assert load_cache_from_disk() is None

            app_cache.build_from_operations(sample_operations)
            roles_by_id = build_roles_by_id(sample_roles)
            app_cache._cache = CacheData(
                all_operations=sample_operations,
                roles_by_id=roles_by_id,
                all_change_events=sample_change_events,
                ops_by_name_lower=app_cache.cache.ops_by_name_lower,
                ops_by_prefix=app_cache.cache.ops_by_prefix,
            )

            metadata = CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            )
            precompute_all_caches(sample_roles, sample_operations, metadata=metadata)
            save_cache_to_disk(app_cache.cache)

            startup_coverage = app_cache.get_role_coverage("reader-role-id")
            assert startup_coverage is not None
            startup_ops = startup_coverage[0]

            # === PHASE 2: Worker Adds New Operation ===
            new_operations = [
                *sample_operations,
                {
                    "name": "Microsoft.NewProvider/resources/read",
                    "display_name": "Read New Resource",
                    "description": "Reads new resource",
                    "provider_display_name": "Microsoft NewProvider",
                    "resource_type_display_name": "Resources",
                    "is_data_action": False,
                },
            ]

            # Worker builds and saves complete cache
            worker_cache = build_complete_cache(sample_roles, new_operations, sample_change_events)
            save_cache_to_disk(worker_cache)

            # === PHASE 3: Web Reloads ===
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                reloaded = app_cache.reload_from_disk_if_needed()

            assert reloaded is True
            assert len(app_cache.cache.all_operations) == len(new_operations)

            # Reader should now cover the new read operation
            reload_coverage = app_cache.get_role_coverage("reader-role-id")
            reload_ops = reload_coverage[0]
            assert len(reload_ops) > len(startup_ops)
            assert any("newprovider" in op.lower() for op in reload_ops)

            # === PHASE 4: Periodic Refresh ===
            role_jsons = app_cache.get_all_role_jsons()
            all_ops = app_cache.cache.all_operations

            precompute_all_caches(role_jsons, all_ops)

            # Coverage should be unchanged
            periodic_coverage = app_cache.get_role_coverage("reader-role-id")
            assert periodic_coverage[0] == reload_ops

    def test_multiple_workers_scenario(self, temp_cache_dir, sample_roles, sample_operations):
        """Multiple workers writing cache - last writer wins."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Worker 1 writes
            worker1_ops = sample_operations[:3]
            cache1 = build_complete_cache(sample_roles, worker1_ops)
            save_cache_to_disk(cache1)

            # Worker 2 writes (overwrites)
            cache2 = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache2)

            # Load should get worker 2's cache
            loaded = load_cache_from_disk()
            assert len(loaded.all_operations) == len(sample_operations)

    def test_web_survives_corrupt_disk_cache(self, temp_cache_dir, sample_roles, sample_operations):
        """Web handles corrupt disk cache gracefully."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Write corrupt data
            cache_file = temp_cache_dir / "app_cache.msgpack"
            cache_file.write_bytes(b"not a valid pickle")

            # Load should return None (not crash)
            loaded = load_cache_from_disk()
            assert loaded is None

            # Web can still build from scratch
            app_cache.build_from_operations(sample_operations)
            assert len(app_cache.cache.all_operations) == len(sample_operations)

    def test_cache_survives_app_restart(self, temp_cache_dir, sample_roles, sample_operations):
        """Cache persists across app restarts."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # First run: build and save
            cache1 = build_complete_cache(sample_roles, sample_operations)
            save_cache_to_disk(cache1)
            original_coverage = dict(cache1.role_coverage)

            # Simulate restart: clear memory
            app_cache._cache = CacheData()
            clear_computed_caches()

            # Load from disk
            loaded = load_cache_from_disk()
            assert loaded is not None
            app_cache.swap(loaded)

            # Data should be identical
            assert app_cache.cache.role_coverage == original_coverage


# =============================================================================
# Startup Flow Tests
# =============================================================================


class TestStartupCacheFlow:
    """Tests for cache initialization at startup."""

    def test_precompute_populates_all_computed_caches(self, sample_roles, sample_operations):
        """Verify precompute_all_caches populates all computed data."""
        # Build source data into the singleton cache
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # Before precompute - no computed data
        assert app_cache.get_role_coverage("reader-role-id") is None

        # Run precompute (uses singleton by default)
        precompute_all_caches(sample_roles, sample_operations)

        # After precompute - computed data available
        coverage = app_cache.get_role_coverage("reader-role-id")
        assert coverage is not None
        control_ops, _data_ops = coverage
        # Reader has */read pattern - matches all control plane read operations
        # (Storage, Compute, Auth, Network, KeyVault = 5)
        assert len(control_ops) == 5

    def test_precompute_is_atomic(self, sample_roles, sample_operations):
        """Verify precompute uses atomic swap pattern."""
        # Set up initial data
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # Run precompute
        precompute_all_caches(sample_roles, sample_operations)

        # Computed fields should now be populated
        assert len(app_cache.cache.role_coverage) > 0
        assert len(app_cache.cache.role_net_permissions) > 0

    def test_startup_builds_cache_from_scratch(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Simulate startup: no disk cache, build from database."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # No disk cache exists
            loaded = load_cache_from_disk()
            assert loaded is None

            # Simulate startup: build operations index
            app_cache.build_from_operations(sample_operations)

            # Build roles index
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            app_cache._cache = CacheData(
                all_operations=sample_operations,
                roles_by_id=roles_by_id,
                ops_by_name_lower=app_cache.cache.ops_by_name_lower,
                ops_by_prefix=app_cache.cache.ops_by_prefix,
            )

            # Precompute all caches
            precompute_all_caches(sample_roles, sample_operations)

            # Verify computed data is available
            assert app_cache.get_role_coverage("reader-role-id") is not None
            assert app_cache.get_role_net_permissions("reader-role-id") is not None


# =============================================================================
# Worker Update Flow Tests
# =============================================================================


class TestWorkerUpdateFlow:
    """Tests for cache reload when worker updates disk cache."""

    def test_worker_saves_cache_to_disk(self, temp_cache_dir, sample_roles, sample_operations):
        """Verify worker can save cache to disk."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Worker builds and saves cache
            roles_hash = compute_roles_hash(sample_roles)
            ops_hash = compute_operations_hash(sample_operations)

            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}

            data = CacheData(
                metadata=CacheMetadata(
                    roles_count=len(sample_roles),
                    operations_count=len(sample_operations),
                    roles_hash=roles_hash,
                    operations_hash=ops_hash,
                ),
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
            )

            result = save_cache_to_disk(data)
            assert result is True

            # Verify file exists
            cache_file = temp_cache_dir / "app_cache.msgpack"
            assert cache_file.exists()

    def test_web_process_detects_disk_update(self, temp_cache_dir, sample_roles, sample_operations):
        """Verify web process detects when worker updates disk cache."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            app_cache._loaded_cache_mtime = 1000.0  # Old mtime
            app_cache._last_cache_check = 0  # Force check

            # Worker saves cache
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            data = CacheData(
                metadata=CacheMetadata(
                    roles_count=len(sample_roles),
                    operations_count=len(sample_operations),
                ),
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
            )
            save_cache_to_disk(data)

            # Web process checks if reload needed
            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                needs_reload = app_cache.needs_reload_from_disk()
                assert needs_reload is True

    def test_reload_from_disk_loads_precomputed_data(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify reload_from_disk_if_needed loads precomputed data directly.

        The disk file contains the complete CacheData including all computed fields.
        No recomputation is needed on reload - just load and swap.
        """
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            # Save cache to disk with precomputed data
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            precomputed_role_coverage = {"reader-role-id": ({"op1"}, {"op2"})}
            data = CacheData(
                metadata=CacheMetadata(
                    roles_count=len(sample_roles),
                    operations_count=len(sample_operations),
                ),
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
                operations_for_recommender=[
                    {"name": op["name"], "is_data_action": op["is_data_action"]}
                    for op in sample_operations
                ],
                unique_providers=["Microsoft.Storage", "Microsoft.Compute"],
                role_coverage=precomputed_role_coverage,
            )
            save_cache_to_disk(data)

            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                result = app_cache.reload_from_disk_if_needed()

            assert result is True
            # Verify the precomputed data was loaded directly
            assert app_cache.cache.role_coverage == precomputed_role_coverage
            assert len(app_cache.cache.roles_by_id) == len(sample_roles)
            assert len(app_cache.cache.all_operations) == len(sample_operations)

    def test_reload_updates_cache_atomically(self, temp_cache_dir, sample_roles, sample_operations):
        """Verify reload replaces cache atomically."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            # Set initial data
            app_cache._cache = CacheData(
                all_operations=[{"name": "old-op", "is_data_action": False}]
            )

            # Save new cache to disk
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            data = CacheData(
                metadata=CacheMetadata(
                    roles_count=len(sample_roles),
                    operations_count=len(sample_operations),
                ),
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
                operations_for_recommender=[],
                unique_providers=[],
            )
            save_cache_to_disk(data)

            # Capture reference before reload
            old_cache = app_cache.cache
            assert len(old_cache.all_operations) == 1

            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                app_cache.reload_from_disk_if_needed()

            # Cache should be new object with new data
            new_cache = app_cache.cache
            assert len(new_cache.all_operations) == len(sample_operations)
            assert len(new_cache.roles_by_id) == len(sample_roles)


# =============================================================================
# Periodic Refresh Flow Tests
# =============================================================================


class TestPeriodicRefreshFlow:
    """Tests for hourly cache refresh."""

    def test_periodic_refresh_recomputes_caches(self, sample_roles, sample_operations):
        """Verify periodic refresh recomputes all caches."""
        # Initial setup
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # First precompute
        precompute_all_caches(sample_roles, sample_operations)
        first_coverage = app_cache.get_role_coverage("reader-role-id")
        assert first_coverage is not None

        # Simulate periodic refresh (recompute same data)
        precompute_all_caches(sample_roles, sample_operations)
        second_coverage = app_cache.get_role_coverage("reader-role-id")

        # Results should be identical
        assert second_coverage is not None
        assert first_coverage[0] == second_coverage[0]  # Control plane ops
        assert first_coverage[1] == second_coverage[1]  # Data plane ops

    def test_periodic_refresh_uses_current_cache_data(self, sample_roles, sample_operations):
        """Verify periodic refresh uses data from current cache, not stale data."""
        # Setup with sample data
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # Get role JSONs from cache (simulates what periodic refresh does)
        role_jsons = app_cache.get_all_role_jsons()
        all_ops = app_cache.cache.all_operations

        assert len(role_jsons) == len(sample_roles)
        assert len(all_ops) == len(sample_operations)

        # Run precompute with cache data
        precompute_all_caches(role_jsons, all_ops)

        # Verify computed data is correct
        coverage = app_cache.get_role_coverage("reader-role-id")
        assert coverage is not None


# =============================================================================
# Invalidation After Data Change Tests
# =============================================================================


class TestInvalidationAfterDataChange:
    """Tests for cache invalidation after data changes."""

    def test_clear_computed_caches_removes_computed_data(self, sample_roles, sample_operations):
        """Verify clear_computed_caches removes computed fields but preserves source data."""
        # Setup with source and computed data
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )
        precompute_all_caches(sample_roles, sample_operations)

        # Verify computed data exists
        assert app_cache.get_role_coverage("reader-role-id") is not None

        # Clear computed caches (uses singleton by default)
        clear_computed_caches()

        # Computed data should be gone
        assert app_cache.get_role_coverage("reader-role-id") is None

        # Source data should be preserved
        assert len(app_cache.cache.all_operations) == len(sample_operations)
        assert len(app_cache.cache.roles_by_id) == len(sample_roles)

    def test_invalidate_all_clears_everything(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify invalidate_all clears all caches including disk."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Save to disk
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            data = CacheData(
                metadata=CacheMetadata(
                    roles_count=len(sample_roles),
                    operations_count=len(sample_operations),
                ),
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
            )
            save_cache_to_disk(data)

            # Populate memory cache
            app_cache.build_from_operations(sample_operations)
            app_cache._role_pages["page1"] = [{"role_id": "test"}]
            app_cache._misc_cache["key1"] = "value1"

            # Invalidate all
            app_cache.invalidate_all()

            # Memory cache cleared
            assert len(app_cache.cache.all_operations) == 0
            assert len(app_cache.cache.roles_by_id) == 0
            assert len(app_cache._role_pages) == 0
            assert len(app_cache._misc_cache) == 0

            # Disk cache deleted
            assert not (temp_cache_dir / "app_cache.msgpack").exists()

    def test_swap_clears_misc_cache(self, sample_operations):
        """Verify atomic swap clears misc_cache (roles_allowing_op, etc.)."""
        # Populate misc cache
        app_cache._misc_cache["roles_allowing_op:test"] = [{"role_id": "test"}]
        assert app_cache.get("roles_allowing_op:test") is not None

        # Atomic swap with new data
        new_cache = CacheData(all_operations=sample_operations)
        app_cache.swap(new_cache)

        # Misc cache should be cleared
        assert app_cache.get("roles_allowing_op:test") is None


# =============================================================================
# End-to-End Integration Tests
# =============================================================================


class TestCacheLifecycleE2E:
    """End-to-end tests for complete cache lifecycle."""

    def test_full_lifecycle_startup_to_refresh(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Test complete lifecycle: startup -> worker update -> reload -> periodic refresh."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # === PHASE 1: Startup ===
            # Build from "database"
            app_cache.build_from_operations(sample_operations)
            roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
            app_cache._cache = CacheData(
                all_operations=sample_operations,
                roles_by_id=roles_by_id,
                ops_by_name_lower=app_cache.cache.ops_by_name_lower,
                ops_by_prefix=app_cache.cache.ops_by_prefix,
            )

            # Precompute all caches
            precompute_all_caches(sample_roles, sample_operations)

            # Verify startup state
            coverage_after_startup = app_cache.get_role_coverage("reader-role-id")
            assert coverage_after_startup is not None
            startup_control_ops = coverage_after_startup[0]

            # === PHASE 2: Worker Update ===
            # Worker adds a new operation
            updated_operations = [
                *sample_operations,
                {"name": "Microsoft.NewService/resources/read", "is_data_action": False},
            ]

            # Worker precomputes and saves complete cache to disk
            # (In real system, rebuild_cache does this)
            roles_by_id_for_save = {
                r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles
            }
            # Precompute using worker's data, but don't swap into our memory
            worker_cache = precompute_all_caches(
                sample_roles,
                updated_operations,
                roles_by_id=roles_by_id_for_save,
                swap_in_memory=False,  # Worker saves to disk, doesn't swap
            )
            save_cache_to_disk(worker_cache)

            # === PHASE 3: Web Reload ===
            app_cache._loaded_cache_mtime = 1000.0
            app_cache._last_cache_check = 0

            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                reloaded = app_cache.reload_from_disk_if_needed()

            assert reloaded is True

            # Verify new operation is in cache
            assert len(app_cache.cache.all_operations) == len(updated_operations)

            # Verify computed data is loaded from disk (no recomputation needed)
            coverage_after_reload = app_cache.get_role_coverage("reader-role-id")
            assert coverage_after_reload is not None
            reload_control_ops = coverage_after_reload[0]

            # Reader should cover new op (*/read matches NewService/resources/read)
            assert len(reload_control_ops) > len(startup_control_ops)

            # === PHASE 4: Periodic Refresh ===
            # Simulate 1 hour later, periodic refresh
            role_jsons = app_cache.get_all_role_jsons()
            all_ops = app_cache.cache.all_operations

            precompute_all_caches(role_jsons, all_ops)

            # Coverage should be unchanged
            coverage_after_periodic = app_cache.get_role_coverage("reader-role-id")
            assert coverage_after_periodic is not None
            assert coverage_after_periodic[0] == reload_control_ops

    def test_data_consistency_across_refresh(self, sample_roles, sample_operations):
        """Verify data remains consistent across multiple refreshes."""
        # Initial setup
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        # Multiple refresh cycles
        results = []
        for _ in range(5):
            precompute_all_caches(sample_roles, sample_operations)
            coverage = app_cache.get_role_coverage("reader-role-id")
            assert coverage is not None
            results.append((len(coverage[0]), len(coverage[1])))

        # All results should be identical
        assert len(set(results)) == 1, f"Inconsistent results across refreshes: {results}"

    def test_no_cache_pollution_across_roles(self, sample_operations):
        """Verify one role's data doesn't pollute another role's cache."""
        # Two roles with different permissions
        roles = [
            {
                "name": "reader-role-id",
                "properties": {
                    "roleName": "Reader",
                    "type": "BuiltInRole",
                    "permissions": [
                        {
                            "actions": ["*/read"],
                            "notActions": [],
                            "dataActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            },
            {
                "name": "writer-role",
                "properties": {
                    "roleName": "Writer",
                    "type": "BuiltInRole",
                    "permissions": [
                        {
                            "actions": ["*/write"],
                            "notActions": [],
                            "dataActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            },
        ]

        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )

        precompute_all_caches(roles, sample_operations)

        reader_coverage = app_cache.get_role_coverage("reader-role-id")
        writer_coverage = app_cache.get_role_coverage("writer-role")

        assert reader_coverage is not None
        assert writer_coverage is not None

        # Reader should only have read operations
        reader_ops = reader_coverage[0]
        for op in reader_ops:
            assert "read" in op.lower(), f"Reader has non-read op: {op}"

        # Writer should only have write operations
        writer_ops = writer_coverage[0]
        for op in writer_ops:
            assert "write" in op.lower(), f"Writer has non-write op: {op}"

        # No overlap between reader and writer
        assert reader_ops.isdisjoint(writer_ops)

    def test_worker_invalidate_and_rebuild_flow(self, sample_roles, sample_operations):
        """Test the worker flow: invalidate -> rebuild -> web reload."""
        # Setup initial state
        app_cache.build_from_operations(sample_operations)
        roles_by_id = {r["name"]: {"role_id": r["name"], "role_json": r} for r in sample_roles}
        app_cache._cache = CacheData(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=app_cache.cache.ops_by_name_lower,
            ops_by_prefix=app_cache.cache.ops_by_prefix,
        )
        precompute_all_caches(sample_roles, sample_operations)

        # Verify initial state
        assert app_cache.get_role_coverage("reader-role-id") is not None

        # Simulate worker calling clear_computed_caches (invalidation)
        clear_computed_caches()

        # Computed data should be cleared
        assert app_cache.get_role_coverage("reader-role-id") is None

        # Source data should still exist
        assert len(app_cache.cache.all_operations) == len(sample_operations)

        # Simulate worker rebuilding cache
        precompute_all_caches(sample_roles, sample_operations)

        # Computed data should be back
        assert app_cache.get_role_coverage("reader-role-id") is not None
