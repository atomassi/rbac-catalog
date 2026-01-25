"""End-to-end tests for cache refresh scenarios.

These tests verify complete cache workflows across all sources:
1. Worker process refresh (rebuild_cache_to_disk -> web reload)
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

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache import (
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    compute_operations_hash,
    compute_roles_hash,
    get_cache_service,
    precompute_all,
)
from azurerbac.cache.models import CACHE_VERSION
from azurerbac.core.constants import RoleStatus
from tests.helpers import clear_computed_caches, populate_cache_with_operations


@pytest.fixture
def sample_change_events():
    """Sample change events for testing."""
    return [
        CachedChangeEvent(
            id=1,
            role_id="reader-role-id",
            role_name="Reader",
            event_type="created",
            scan_timestamp=datetime(2024, 1, 1, 12, 0, 0),
            azure_updated_on=datetime(2024, 1, 1, 10, 0, 0),
            summary="Role created",
            diff_json=None,
            role_json={
                "id": "reader-role-id",
                "properties": {"roleName": "Reader", "type": "BuiltInRole"},
            },
        ),
        CachedChangeEvent(
            id=2,
            role_id="storage-data-reader-id",
            role_name="Storage Data Reader",
            event_type="updated",
            scan_timestamp=datetime(2024, 1, 15, 12, 0, 0),
            azure_updated_on=datetime(2024, 1, 15, 10, 0, 0),
            summary="Permissions updated",
            diff_json={"changes": [{"field": "permissions", "old": "X", "new": "Y"}]},
            role_json={
                "id": "storage-data-reader-id",
                "properties": {"roleName": "Storage Data Reader", "type": "BuiltInRole"},
            },
        ),
    ]


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache testing and configure backend."""
    from azurerbac.cache.backends.file import FileCacheBackend

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)
        # Configure the backend to use this temp directory
        backend = get_cache_service().backend
        if isinstance(backend, FileCacheBackend):
            backend.cache_dir = temp_path
        yield temp_path


@pytest.fixture(autouse=True)
def clean_cache():
    """Clean up caches before and after each test."""
    container = get_cache_service().container
    # Clear before test
    container.reset()
    clear_computed_caches(container)
    yield
    # Clear after test
    container.reset()
    clear_computed_caches(container)


def build_roles_by_id(roles: list[RoleDefinition]) -> dict[str, CachedRole]:
    """Helper to build roles_by_id index from role list."""
    return {
        r.role_id: CachedRole(
            definition=r,
            status=RoleStatus.ACTIVE,
            last_seen_at=None,
        )
        for r in roles
    }


def build_complete_cache(
    roles: list[RoleDefinition],
    operations: list[OperationData],
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

    cache_data = precompute_all(
        roles,
        operations,
        metadata=metadata,
        roles_by_id=roles_by_id,
        all_change_events=change_events or [],
    )
    return cache_data


# =============================================================================
# Worker Refresh Flow Tests
# =============================================================================


class TestWorkerRefreshFlow:
    """Tests for worker process cache refresh (the primary refresh mechanism)."""

    async def test_worker_builds_complete_cache_and_saves_to_disk(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Worker builds complete cache with all computed fields and saves to disk."""
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
        assert len(cache_data.unique_providers) > 0

        # Save to disk
        result = await get_cache_service().backend.save(cache_data)
        assert result is True

        # Verify file exists and is substantial
        cache_file = temp_cache_dir / "app_cache.msgpack"
        assert cache_file.exists()
        assert cache_file.stat().st_size > 1000  # Should be several KB

    async def test_web_process_loads_precomputed_cache_from_disk(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Web process loads complete cache from disk - no recomputation needed."""
        # Worker saves cache with precomputed data
        cache_data = build_complete_cache(sample_roles, sample_operations)
        original_coverage = dict(cache_data.role_coverage)
        await get_cache_service().backend.save(cache_data)

        # Web process loads from disk
        loaded = await get_cache_service().backend.load()

        assert loaded is not None
        assert loaded.metadata.version == CACHE_VERSION
        assert len(loaded.roles_by_id) == len(sample_roles)
        assert len(loaded.all_operations) == len(sample_operations)

        # Precomputed fields should be loaded directly
        assert loaded.role_coverage == original_coverage
        assert len(loaded.role_net_permissions) == len(sample_roles)

    async def test_web_detects_worker_update_and_reloads(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Web process detects worker disk update and reloads cache."""
        # Initial state - web has old cache
        old_role_def = RoleDefinition.model_validate({"name": "old-role", "properties": {}})
        old_cache = CacheData.create(
            metadata=CacheMetadata(roles_count=1, operations_count=1),
            all_operations=[OperationData(name="old-op", is_data_action=False)],
            roles_by_id={"old-role": CachedRole(definition=old_role_def, status=RoleStatus.ACTIVE)},
        )
        get_cache_service().container.swap(old_cache)
        get_cache_service().container._loaded_version = "1000.0"

        # Worker saves new cache
        new_cache = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(new_cache)

        # Web detects update and reloads
        # (single call since needs_reload updates _last_cache_check)
        result = await get_cache_service().reload_if_needed()
        assert result is True

        # Verify web now has new data
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)
        assert len(get_cache_service().container.cache.roles_by_id) == len(sample_roles)
        assert len(get_cache_service().container.cache.role_coverage) == len(sample_roles)

    async def test_reload_is_atomic_no_partial_state(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Reload is atomic - readers never see partial state."""
        # Setup initial cache
        old_ops = [OperationData(name="old-op", is_data_action=False)]
        populate_cache_with_operations(get_cache_service().container, old_ops)
        get_cache_service().container._loaded_version = "1000.0"

        # Save new cache to disk
        new_cache = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(new_cache)

        # Capture reference before reload
        old_cache_ref = get_cache_service().container.cache
        assert len(old_cache_ref.all_operations) == 1

        # Reload
        await get_cache_service().reload_if_needed()

        # New cache is different object
        new_cache_ref = get_cache_service().container.cache
        assert new_cache_ref is not old_cache_ref
        assert len(new_cache_ref.all_operations) == len(sample_operations)

        # Old reference is unchanged (readers with old ref see consistent data)
        assert len(old_cache_ref.all_operations) == 1

    def test_worker_refresh_clears_recommender_caches_before_rebuild(
        self, sample_roles, sample_operations
    ):
        """Worker clears computed caches before rebuilding to ensure consistency."""
        # Setup with computed data
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Verify computed data exists
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None

        # Simulate worker clear (before rebuild)
        clear_computed_caches()

        # Computed data should be cleared
        assert get_cache_service().container.get_role_coverage("reader-role-id") is None

        # Source data preserved
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)


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
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)

        # First compute
        get_cache_service().swap_in_memory(
            precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        )
        first_coverage = get_cache_service().container.get_role_coverage("reader-role-id")

        # Simulate 1 hour later - periodic refresh uses data from cache
        role_definitions = get_cache_service().container.cache.get_role_definitions()
        all_ops = get_cache_service().container.cache.all_operations
        current_roles_by_id = get_cache_service().container.cache.roles_by_id

        # Recompute
        get_cache_service().swap_in_memory(
            precompute_all(role_definitions, all_ops, roles_by_id=current_roles_by_id)
        )
        second_coverage = get_cache_service().container.get_role_coverage("reader-role-id")

        # Results should be identical
        assert first_coverage == second_coverage

    def test_periodic_refresh_updates_pattern_match_cache(self, sample_roles, sample_operations):
        """Periodic refresh updates all pattern caches."""
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Pattern match cache should be populated
        assert len(get_cache_service().container.cache.pattern_match) > 0

    async def test_multiple_periodic_refreshes_are_consistent(
        self, sample_roles, sample_operations
    ):
        """Multiple periodic refreshes produce identical results."""
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        results = []
        for _ in range(5):
            get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
            coverage = get_cache_service().container.get_role_coverage("reader-role-id")
            net_perms = get_cache_service().container.get_role_net_permissions("reader-role-id")
            results.append((len(coverage[0]), len(coverage[1]), net_perms))

        # All should be identical
        assert len(set(results)) == 1


# =============================================================================
# Web Startup Flow Tests
# =============================================================================


class TestWebStartupFlow:
    """Tests for web app startup cache initialization."""

    async def test_startup_with_valid_disk_cache_loads_directly(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with valid disk cache loads directly - no DB query needed."""
        # Previous run saved cache
        cache_data = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache_data)

        # Startup loads from disk
        loaded = await get_cache_service().backend.load()

        assert loaded is not None
        assert len(loaded.roles_by_id) == len(sample_roles)
        assert len(loaded.all_operations) == len(sample_operations)
        # Computed fields are already populated
        assert len(loaded.role_coverage) == len(sample_roles)

    async def test_startup_with_no_disk_cache_builds_from_scratch(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with no disk cache builds from database."""
        # No disk cache
        loaded = await get_cache_service().backend.load()
        assert loaded is None

        # Build from "database" (simulated)
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Precompute
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Save for next startup
        await get_cache_service().backend.save(get_cache_service().container.cache)

        # Verify data is available
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None

    async def test_startup_with_stale_cache_rebuilds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with stale disk cache (wrong counts) triggers rebuild."""
        # Save cache with 3 roles
        cache_data = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache_data)

        # Startup with different counts (simulating DB has more data)
        loaded = await get_cache_service().backend.load()
        assert loaded is not None

        # Check if valid for different counts
        is_valid = loaded.metadata.is_valid_for(100, 1000)  # Different counts
        assert is_valid is False  # Should be invalid

    async def test_startup_with_old_version_cache_rebuilds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Startup with old cache version triggers rebuild."""
        # Save cache with old version
        cache_data = build_complete_cache(sample_roles, sample_operations)
        # Manually set old version
        old_metadata = CacheMetadata(
            roles_count=len(sample_roles),
            operations_count=len(sample_operations),
        )
        old_metadata.version = "v1"  # Old version
        cache_data = CacheData.create(
            metadata=old_metadata,
            roles_by_id=cache_data.roles_by_id,
            all_operations=cache_data.all_operations,
        )
        await get_cache_service().backend.save(cache_data)

        # Startup tries to reload
        get_cache_service().container._loaded_version = "1000.0"

        result = await get_cache_service().reload_if_needed()

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

    async def test_cache_includes_all_computed_fields(self, sample_roles, sample_operations):
        """Cache includes all computed fields for direct load."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        # Source data
        assert len(cache_data.all_operations) == len(sample_operations)
        assert len(cache_data.roles_by_id) == len(sample_roles)

        # Derived data
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

    async def test_concurrent_reloads_only_one_proceeds(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Only one concurrent reload proceeds, others skip."""
        import asyncio

        # Save cache
        cache_data = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache_data)

        get_cache_service().container._loaded_version = "1000.0"

        results = []
        errors = []

        async def attempt_reload(task_id):
            try:
                result = await get_cache_service().reload_if_needed()
                results.append((task_id, result))
            except Exception as e:
                errors.append((task_id, str(e)))

        # Start multiple concurrent tasks
        tasks = [asyncio.create_task(attempt_reload(i)) for i in range(5)]
        await asyncio.gather(*tasks)

        assert len(errors) == 0
        # At most one should succeed (first one to acquire lock)
        successful = [r for r in results if r[1] is True]
        assert len(successful) <= 1

    def test_readers_see_consistent_data_during_swap(self, sample_roles, sample_operations):
        """Readers always see consistent data even during cache swap."""
        # Setup initial cache
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        read_results = []
        errors = []

        def reader(reader_id):
            """Read multiple times and verify consistency."""
            try:
                for _ in range(100):
                    cache = get_cache_service().container.cache  # Capture reference
                    ops_count = len(cache.all_operations)
                    roles_count = len(cache.roles_by_id)
                    # These should be consistent within single read
                    read_results.append((reader_id, ops_count, roles_count))
            except Exception as e:
                errors.append((reader_id, str(e)))

        def writer():
            """Swap cache multiple times."""
            for _ in range(50):
                new_cache = CacheData.create(
                    all_operations=sample_operations,
                    roles_by_id=roles_by_id,
                )
                get_cache_service().container.swap(new_cache)

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

    def test_unique_providers_extracted_correctly(self, sample_roles, sample_operations):
        """unique_providers contains all providers from operations."""
        cache_data = build_complete_cache(sample_roles, sample_operations)

        expected_providers = {
            op.provider_display_name for op in sample_operations if op.provider_display_name
        }

        assert set(cache_data.unique_providers) == expected_providers

    async def test_no_role_data_pollution_between_roles(self, sample_operations):
        """One role's computed data doesn't pollute another role's cache."""
        role_dicts = [
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
        roles = [RoleDefinition.model_validate(r) for r in role_dicts]

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

    async def test_change_events_preserved_through_cache_cycle(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Change events are preserved through save/load cycle."""
        # Build and save
        cache_data = build_complete_cache(sample_roles, sample_operations, sample_change_events)
        await get_cache_service().backend.save(cache_data)

        # Load
        loaded = await get_cache_service().backend.load()

        assert len(loaded.all_change_events) == len(sample_change_events)
        for i, event in enumerate(loaded.all_change_events):
            assert event.role_id == sample_change_events[i].role_id
            assert event.event_type == sample_change_events[i].event_type

    async def test_change_events_include_role_json(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Change events include role_json for created/updated events.

        This is required for the Change History to display the full JSON
        for 'created' events, especially important for deleted roles where
        the current role_json is NULL.
        """
        # Build and save
        cache_data = build_complete_cache(sample_roles, sample_operations, sample_change_events)
        await get_cache_service().backend.save(cache_data)

        # Load
        loaded = await get_cache_service().backend.load()

        # Verify role_json is preserved
        for i, event in enumerate(loaded.all_change_events):
            original = sample_change_events[i]
            assert event.role_json is not None or original.role_json is None, (
                f"Event {i} missing role_json"
            )
            if original.role_json:
                assert event.role_json == original.role_json
                assert event.role_json["properties"]["roleName"]


# =============================================================================
# Invalidation Flow Tests
# =============================================================================


class TestInvalidationFlow:
    """Tests for cache invalidation scenarios."""

    async def test_delete_cache_file_removes_disk_file(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """backend.delete() removes the disk cache file."""
        # Save cache
        cache_data = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache_data)

        cache_file = temp_cache_dir / "app_cache.msgpack"
        assert cache_file.exists()

        # Delete cache file
        await get_cache_service().backend.delete()
        assert not cache_file.exists()

    async def test_invalidate_all_clears_everything(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """invalidate_all clears all caches including memory and disk."""
        # Setup cache
        cache_data = build_complete_cache(sample_roles, sample_operations)
        get_cache_service().container.swap(cache_data)
        get_cache_service().container.set_role_page("page1", [{"test": True}])
        get_cache_service().container.set_allowing_roles("key1", [])
        await get_cache_service().backend.save(get_cache_service().container.cache)

        # Verify everything exists
        assert len(get_cache_service().container.cache.all_operations) > 0
        assert get_cache_service().container.get_role_page("page1") is not None
        assert get_cache_service().container.get_allowing_roles("key1") is not None
        assert (temp_cache_dir / "app_cache.msgpack").exists()

        # Invalidate all
        await get_cache_service().invalidate_all()

        # Everything should be cleared
        assert len(get_cache_service().container.cache.all_operations) == 0
        assert len(get_cache_service().container.cache.roles_by_id) == 0
        assert get_cache_service().container.get_role_page("page1") is None
        assert get_cache_service().container.get_allowing_roles("key1") is None
        assert not (temp_cache_dir / "app_cache.msgpack").exists()

    def test_swap_clears_allowing_roles_cache(self, sample_operations):
        """Atomic swap clears allowing_roles_cache."""
        # Populate allowing_roles cache
        container = get_cache_service().container
        container.set_allowing_roles("roles_allowing_op:test", [])
        assert container.get_allowing_roles("roles_allowing_op:test") is not None

        # Swap with new cache
        new_cache = CacheData.create(all_operations=sample_operations)
        container.swap(new_cache)

        # allowing_roles_cache should be cleared (new CacheData = fresh RequestCaches)
        assert get_cache_service().container.get_allowing_roles("roles_allowing_op:test") is None


# =============================================================================
# Full Lifecycle Integration Tests
# =============================================================================


class TestFullLifecycleE2E:
    """End-to-end tests for complete cache lifecycle."""

    async def test_complete_lifecycle_startup_worker_reload_refresh(
        self, temp_cache_dir, sample_roles, sample_operations, sample_change_events
    ):
        """Test complete lifecycle: startup -> worker update -> web reload -> periodic refresh."""
        # === PHASE 1: Cold Start ===
        # No disk cache, web builds from DB
        assert await get_cache_service().backend.load() is None

        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            all_change_events=sample_change_events,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        metadata = CacheMetadata(
            roles_count=len(sample_roles),
            operations_count=len(sample_operations),
        )
        get_cache_service().swap_in_memory(
            precompute_all(sample_roles, sample_operations, metadata=metadata)
        )
        await get_cache_service().backend.save(get_cache_service().container.cache)

        startup_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert startup_coverage is not None
        startup_ops = startup_coverage[0]

        # === PHASE 2: Worker Adds New Operation ===
        new_operations = [
            *sample_operations,
            OperationData(
                name="Microsoft.NewProvider/resources/read",
                display_name="Read New Resource",
                description="Reads new resource",
                provider_display_name="Microsoft NewProvider",
                resource_type_display_name="Resources",
                is_data_action=False,
            ),
        ]

        # Worker builds and saves complete cache
        worker_cache = build_complete_cache(sample_roles, new_operations, sample_change_events)
        await get_cache_service().backend.save(worker_cache)

        # === PHASE 3: Web Reloads ===
        get_cache_service().container._loaded_version = "1000.0"

        reloaded = await get_cache_service().reload_if_needed()

        assert reloaded is True
        assert len(get_cache_service().container.cache.all_operations) == len(new_operations)

        # Reader should now cover the new read operation
        reload_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        reload_ops = reload_coverage[0]
        assert len(reload_ops) > len(startup_ops)
        assert any("newprovider" in op.lower() for op in reload_ops)

        # === PHASE 4: Periodic Refresh ===
        role_definitions = get_cache_service().container.cache.get_role_definitions()
        all_ops = get_cache_service().container.cache.all_operations

        get_cache_service().swap_in_memory(precompute_all(role_definitions, all_ops))

        # Coverage should be unchanged
        periodic_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert periodic_coverage[0] == reload_ops

    @pytest.mark.asyncio
    async def test_multiple_workers_scenario(self, temp_cache_dir, sample_roles, sample_operations):
        """Multiple workers writing cache - last writer wins."""
        # Worker 1 writes
        worker1_ops = sample_operations[:3]
        cache1 = build_complete_cache(sample_roles, worker1_ops)
        await get_cache_service().backend.save(cache1)

        # Worker 2 writes (overwrites)
        cache2 = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache2)

        # Load should get worker 2's cache
        loaded = await get_cache_service().backend.load()
        assert len(loaded.all_operations) == len(sample_operations)

    @pytest.mark.asyncio
    async def test_web_survives_corrupt_disk_cache(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Web handles corrupt disk cache gracefully."""
        # Write corrupt data
        cache_file = temp_cache_dir / "app_cache.msgpack"
        cache_file.write_bytes(b"not a valid pickle")

        # Load should return None (not crash)
        loaded = await get_cache_service().backend.load()
        assert loaded is None

        # Web can still build from scratch
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)

    @pytest.mark.asyncio
    async def test_cache_survives_app_restart(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Cache persists across app restarts."""
        # First run: build and save
        cache1 = build_complete_cache(sample_roles, sample_operations)
        await get_cache_service().backend.save(cache1)
        original_coverage = dict(cache1.role_coverage)

        # Simulate restart: clear memory
        get_cache_service().container._cache = CacheData()
        clear_computed_caches()

        # Load from disk
        loaded = await get_cache_service().backend.load()
        assert loaded is not None
        get_cache_service().container.swap(loaded)

        # Data should be identical
        assert get_cache_service().container.cache.role_coverage == original_coverage


# =============================================================================
# Startup Flow Tests
# =============================================================================


class TestStartupCacheFlow:
    """Tests for cache initialization at startup."""

    def test_precompute_populates_all_computed_caches(self, sample_roles, sample_operations):
        """Verify precompute_all populates all computed data."""
        # Build source data into the singleton cache
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Before precompute - no computed data
        assert get_cache_service().container.get_role_coverage("reader-role-id") is None

        # Run precompute (uses singleton by default)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # After precompute - computed data available
        coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert coverage is not None
        control_ops, _data_ops = coverage
        # Reader has */read pattern - matches all control plane read operations
        # (Storage, Compute, Auth, Network, KeyVault = 5)
        assert len(control_ops) == 5

    def test_precompute_is_atomic(self, sample_roles, sample_operations):
        """Verify precompute uses atomic swap pattern."""
        # Set up initial data
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Run precompute
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Computed fields should now be populated
        assert len(get_cache_service().container.cache.role_coverage) > 0
        assert len(get_cache_service().container.cache.role_net_permissions) > 0

    async def test_startup_builds_cache_from_scratch(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Simulate startup: no disk cache, build from database."""
        # No disk cache exists
        loaded = await get_cache_service().backend.load()
        assert loaded is None

        # Simulate startup: build operations index
        populate_cache_with_operations(get_cache_service().container, sample_operations)

        # Build roles index
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Precompute all caches
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Verify computed data is available
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None
        assert get_cache_service().container.get_role_net_permissions("reader-role-id") is not None


# =============================================================================
# Worker Update Flow Tests
# =============================================================================


class TestWorkerUpdateFlow:
    """Tests for cache reload when worker updates disk cache."""

    @pytest.mark.asyncio
    async def test_worker_saves_cache_to_disk(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify worker can save cache to disk."""
        # Worker builds and saves cache
        roles_hash = compute_roles_hash(sample_roles)
        ops_hash = compute_operations_hash(sample_operations)

        roles_by_id = build_roles_by_id(sample_roles)

        data = CacheData.create(
            metadata=CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
                roles_hash=roles_hash,
                operations_hash=ops_hash,
            ),
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
        )

        result = await get_cache_service().backend.save(data)
        assert result is True

        # Verify file exists
        cache_file = temp_cache_dir / "app_cache.msgpack"
        assert cache_file.exists()

    @pytest.mark.asyncio
    async def test_web_process_detects_disk_update(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify web process detects when worker updates disk cache."""
        get_cache_service().container._loaded_version = "1000.0"  # Old version

        # Worker saves cache
        roles_by_id = build_roles_by_id(sample_roles)
        data = CacheData.create(
            metadata=CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            ),
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
        )
        await get_cache_service().backend.save(data)

        # Web process checks if reload needed (version comparison)
        result = get_cache_service().needs_reload()
        assert result is True

    async def test_reload_from_disk_loads_precomputed_data(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify reload_from_disk_if_needed loads precomputed data directly.

        The disk file contains the complete CacheData including all computed fields.
        No recomputation is needed on reload - just load and swap.
        """
        get_cache_service().container._loaded_version = "1000.0"

        # Save cache to disk with precomputed data
        roles_by_id = build_roles_by_id(sample_roles)
        precomputed_role_coverage = {"reader-role-id": ({"op1"}, {"op2"})}
        data = CacheData.create(
            metadata=CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            ),
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
            unique_providers=["Microsoft.Storage", "Microsoft.Compute"],
            role_coverage=precomputed_role_coverage,
        )
        await get_cache_service().backend.save(data)

        result = await get_cache_service().reload_if_needed()

        assert result is True
        # Verify the precomputed data was loaded directly
        assert get_cache_service().container.cache.role_coverage == precomputed_role_coverage
        assert len(get_cache_service().container.cache.roles_by_id) == len(sample_roles)
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)

    @pytest.mark.asyncio
    async def test_reload_updates_cache_atomically(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify reload replaces cache atomically."""
        get_cache_service().container._loaded_version = "1000.0"

        # Set initial data
        get_cache_service().container._cache = CacheData.create(
            all_operations=[OperationData(name="old-op", is_data_action=False)]
        )

        # Save new cache to disk
        roles_by_id = build_roles_by_id(sample_roles)
        data = CacheData.create(
            metadata=CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            ),
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
            unique_providers=[],
        )
        await get_cache_service().backend.save(data)

        # Capture reference before reload
        old_cache = get_cache_service().container.cache
        assert len(old_cache.all_operations) == 1

        await get_cache_service().reload_if_needed()

        # Cache should be new object with new data
        new_cache = get_cache_service().container.cache
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
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # First precompute
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        first_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert first_coverage is not None

        # Simulate periodic refresh (recompute same data)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        second_coverage = get_cache_service().container.get_role_coverage("reader-role-id")

        # Results should be identical
        assert second_coverage is not None
        assert first_coverage[0] == second_coverage[0]  # Control plane ops
        assert first_coverage[1] == second_coverage[1]  # Data plane ops

    def test_periodic_refresh_uses_current_cache_data(self, sample_roles, sample_operations):
        """Verify periodic refresh uses data from current cache, not stale data."""
        # Setup with sample data
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Get role JSONs from cache (simulates what periodic refresh does)
        role_definitions = get_cache_service().container.cache.get_role_definitions()
        all_ops = get_cache_service().container.cache.all_operations

        assert len(role_definitions) == len(sample_roles)
        assert len(all_ops) == len(sample_operations)

        # Run precompute with cache data
        get_cache_service().swap_in_memory(precompute_all(role_definitions, all_ops))

        # Verify computed data is correct
        coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert coverage is not None


# =============================================================================
# Invalidation After Data Change Tests
# =============================================================================


class TestInvalidationAfterDataChange:
    """Tests for cache invalidation after data changes."""

    def test_clear_computed_caches_removes_computed_data(self, sample_roles, sample_operations):
        """Verify clear_computed_caches removes computed fields but preserves source data."""
        # Setup with source and computed data
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().swap_in_memory(
            precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        )

        # Verify computed data exists
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None

        # Clear computed caches (uses singleton by default)
        clear_computed_caches()

        # Computed data should be gone
        assert get_cache_service().container.get_role_coverage("reader-role-id") is None

        # Source data should be preserved
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)
        assert len(get_cache_service().container.cache.roles_by_id) == len(sample_roles)

    async def test_invalidate_all_clears_everything(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Verify invalidate_all clears all caches including disk."""
        # Save to disk
        roles_by_id = build_roles_by_id(sample_roles)
        data = CacheData.create(
            metadata=CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            ),
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
        )
        await get_cache_service().backend.save(data)

        # Populate memory cache
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        get_cache_service().container.set_role_page("page1", [{"role_id": "test"}])
        get_cache_service().container.set_allowing_roles("key1", [])

        # Invalidate all
        await get_cache_service().invalidate_all()

        # Memory cache cleared
        assert len(get_cache_service().container.cache.all_operations) == 0
        assert len(get_cache_service().container.cache.roles_by_id) == 0
        assert get_cache_service().container.get_role_page("page1") is None
        assert get_cache_service().container.get_allowing_roles("key1") is None

        # Disk cache deleted
        assert not (temp_cache_dir / "app_cache.msgpack").exists()

    def test_swap_clears_allowing_roles_cache(self, sample_operations):
        """Verify atomic swap clears allowing_roles_cache."""
        # Populate allowing_roles cache
        container = get_cache_service().container
        container.set_allowing_roles("roles_allowing_op:test", [])
        assert container.get_allowing_roles("roles_allowing_op:test") is not None

        # Atomic swap with new data
        new_cache = CacheData.create(all_operations=sample_operations)
        container.swap(new_cache)

        # allowing_roles_cache should be cleared (new CacheData = fresh RequestCaches)
        assert get_cache_service().container.get_allowing_roles("roles_allowing_op:test") is None


# =============================================================================
# End-to-End Integration Tests
# =============================================================================


class TestCacheLifecycleE2E:
    """End-to-end tests for complete cache lifecycle."""

    async def test_full_lifecycle_startup_to_refresh(
        self, temp_cache_dir, sample_roles, sample_operations
    ):
        """Test complete lifecycle: startup -> worker update -> reload -> periodic refresh."""
        # === PHASE 1: Startup ===
        # Build from "database"
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Precompute all caches
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Verify startup state
        coverage_after_startup = get_cache_service().container.get_role_coverage("reader-role-id")
        assert coverage_after_startup is not None
        startup_control_ops = coverage_after_startup[0]

        # === PHASE 2: Worker Update ===
        # Worker adds a new operation
        updated_operations = [
            *sample_operations,
            OperationData(name="Microsoft.NewService/resources/read", is_data_action=False),
        ]

        # Worker precomputes and saves complete cache to disk
        # (In real system, rebuild_cache_to_disk does this)
        roles_by_id_for_save = build_roles_by_id(sample_roles)
        # Precompute using worker's data, but don't swap into our memory
        worker_cache = precompute_all(
            sample_roles,
            updated_operations,
            roles_by_id=roles_by_id_for_save,
        )
        await get_cache_service().backend.save(worker_cache)

        # === PHASE 3: Web Reload ===
        get_cache_service().container._loaded_version = "1000.0"

        reloaded = await get_cache_service().reload_if_needed()

        assert reloaded is True

        # Verify new operation is in cache
        assert len(get_cache_service().container.cache.all_operations) == len(updated_operations)

        # Verify computed data is loaded from disk (no recomputation needed)
        coverage_after_reload = get_cache_service().container.get_role_coverage("reader-role-id")
        assert coverage_after_reload is not None
        reload_control_ops = coverage_after_reload[0]

        # Reader should cover new op (*/read matches NewService/resources/read)
        assert len(reload_control_ops) > len(startup_control_ops)

        # === PHASE 4: Periodic Refresh ===
        # Simulate 1 hour later, periodic refresh
        role_definitions = get_cache_service().container.cache.get_role_definitions()
        all_ops = get_cache_service().container.cache.all_operations

        get_cache_service().swap_in_memory(precompute_all(role_definitions, all_ops))

        # Coverage should be unchanged
        coverage_after_periodic = get_cache_service().container.get_role_coverage("reader-role-id")
        assert coverage_after_periodic is not None
        assert coverage_after_periodic[0] == reload_control_ops

    def test_data_consistency_across_refresh(self, sample_roles, sample_operations):
        """Verify data remains consistent across multiple refreshes."""
        # Initial setup
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        # Multiple refresh cycles
        results = []
        for _ in range(5):
            get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
            coverage = get_cache_service().container.get_role_coverage("reader-role-id")
            assert coverage is not None
            results.append((len(coverage[0]), len(coverage[1])))

        # All results should be identical
        assert len(set(results)) == 1, f"Inconsistent results across refreshes: {results}"

    def test_no_cache_pollution_across_roles(self, sample_operations):
        """Verify one role's data doesn't pollute another role's cache."""
        # Two roles with different permissions
        role_dicts = [
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
        roles = [RoleDefinition.model_validate(r) for r in role_dicts]

        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = {r.role_id: {"role_id": r.role_id, "role_json": r.to_dict()} for r in roles}
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )

        get_cache_service().swap_in_memory(precompute_all(roles, sample_operations))

        reader_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        writer_coverage = get_cache_service().container.get_role_coverage("writer-role")

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
        populate_cache_with_operations(get_cache_service().container, sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().container._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().container.cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().container.cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Verify initial state
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None

        # Simulate worker calling clear_computed_caches (invalidation)
        clear_computed_caches()

        # Computed data should be cleared
        assert get_cache_service().container.get_role_coverage("reader-role-id") is None

        # Source data should still exist
        assert len(get_cache_service().container.cache.all_operations) == len(sample_operations)

        # Simulate worker rebuilding cache
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Computed data should be back
        assert get_cache_service().container.get_role_coverage("reader-role-id") is not None
