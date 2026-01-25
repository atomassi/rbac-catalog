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

import threading
from datetime import datetime

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


@pytest.fixture(autouse=True)
def clean_cache():
    """Clean up caches before and after each test."""
    service = get_cache_service()
    # Clear before test
    service.reset()
    clear_computed_caches(service)
    yield
    # Clear after test
    service.reset()
    clear_computed_caches(service)


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
# Periodic Web Refresh Flow Tests
# =============================================================================


class TestPeriodicWebRefreshFlow:
    """Tests for periodic cache refresh in web process (hourly)."""

    def test_periodic_refresh_recomputes_from_current_cache_data(
        self, sample_roles, sample_operations
    ):
        """Periodic refresh recomputes using current cache data."""
        # Initial setup
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)

        # First compute
        get_cache_service().swap_in_memory(
            precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        )
        first_coverage = get_cache_service().get_role_coverage("reader-role-id")

        # Simulate 1 hour later - periodic refresh uses data from cache
        role_definitions = get_cache_service().cache.get_role_definitions()
        all_ops = get_cache_service().cache.all_operations
        current_roles_by_id = get_cache_service().cache.roles_by_id

        # Recompute
        get_cache_service().swap_in_memory(
            precompute_all(role_definitions, all_ops, roles_by_id=current_roles_by_id)
        )
        second_coverage = get_cache_service().get_role_coverage("reader-role-id")

        # Results should be identical
        assert first_coverage == second_coverage

    def test_periodic_refresh_updates_pattern_match_cache(self, sample_roles, sample_operations):
        """Periodic refresh updates all pattern caches."""
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Pattern match cache should be populated
        assert len(get_cache_service().cache.pattern_match) > 0

    async def test_multiple_periodic_refreshes_are_consistent(
        self, sample_roles, sample_operations
    ):
        """Multiple periodic refreshes produce identical results."""
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        results = []
        for _ in range(5):
            get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
            coverage = get_cache_service().get_role_coverage("reader-role-id")
            net_perms = get_cache_service().get_role_net_permissions("reader-role-id")
            results.append((len(coverage[0]), len(coverage[1]), net_perms))

        # All should be identical
        assert len(set(results)) == 1


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

    def test_readers_see_consistent_data_during_swap(self, sample_roles, sample_operations):
        """Readers always see consistent data even during cache swap."""
        # Setup initial cache
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        read_results = []
        errors = []

        def reader(reader_id):
            """Read multiple times and verify consistency."""
            try:
                for _ in range(100):
                    cache = get_cache_service().cache  # Capture reference
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
                get_cache_service().swap(new_cache)

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


# =============================================================================
# Invalidation Flow Tests
# =============================================================================


class TestInvalidationFlow:
    """Tests for cache invalidation scenarios."""

    async def test_invalidate_all_clears_memory_cache(self, sample_roles, sample_operations):
        """invalidate_all clears all in-memory caches."""
        # Setup cache
        cache_data = build_complete_cache(sample_roles, sample_operations)
        get_cache_service().swap(cache_data)
        get_cache_service().set_role_page("page1", [{"test": True}])
        get_cache_service().set_allowing_roles("key1", [])

        # Verify everything exists
        assert len(get_cache_service().cache.all_operations) > 0
        assert get_cache_service().get_role_page("page1") is not None
        assert get_cache_service().get_allowing_roles("key1") is not None

        # Invalidate all
        await get_cache_service().invalidate_all()

        # Everything should be cleared
        assert len(get_cache_service().cache.all_operations) == 0
        assert len(get_cache_service().cache.roles_by_id) == 0
        assert get_cache_service().get_role_page("page1") is None
        assert get_cache_service().get_allowing_roles("key1") is None

    def test_swap_clears_allowing_roles_cache(self, sample_operations):
        """Atomic swap clears allowing_roles_cache."""
        # Populate allowing_roles cache
        container = get_cache_service()
        container.set_allowing_roles("roles_allowing_op:test", [])
        assert container.get_allowing_roles("roles_allowing_op:test") is not None

        # Swap with new cache
        new_cache = CacheData.create(all_operations=sample_operations)
        container.swap(new_cache)

        # allowing_roles_cache should be cleared (new CacheData = fresh RequestCaches)
        assert get_cache_service().get_allowing_roles("roles_allowing_op:test") is None


# =============================================================================
# Startup Flow Tests
# =============================================================================


class TestStartupCacheFlow:
    """Tests for cache initialization at startup."""

    def test_precompute_populates_all_computed_caches(self, sample_roles, sample_operations):
        """Verify precompute_all populates all computed data."""
        # Build source data into the singleton cache
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        # Before precompute - no computed data
        assert get_cache_service().get_role_coverage("reader-role-id") is None

        # Run precompute (uses singleton by default)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # After precompute - computed data available
        coverage = get_cache_service().get_role_coverage("reader-role-id")
        assert coverage is not None
        control_ops, _data_ops = coverage
        # Reader has */read pattern - matches all control plane read operations
        # (Storage, Compute, Auth, Network, KeyVault = 5)
        assert len(control_ops) == 5

    def test_precompute_is_atomic(self, sample_roles, sample_operations):
        """Verify precompute uses atomic swap pattern."""
        # Set up initial data
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        # Run precompute
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Computed fields should now be populated
        assert len(get_cache_service().cache.role_coverage) > 0
        assert len(get_cache_service().cache.role_net_permissions) > 0


# =============================================================================
# Periodic Refresh Flow Tests
# =============================================================================


class TestPeriodicRefreshFlow:
    """Tests for hourly cache refresh."""

    def test_periodic_refresh_recomputes_caches(self, sample_roles, sample_operations):
        """Verify periodic refresh recomputes all caches."""
        # Initial setup
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        # First precompute
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        first_coverage = get_cache_service().get_role_coverage("reader-role-id")
        assert first_coverage is not None

        # Simulate periodic refresh (recompute same data)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        second_coverage = get_cache_service().get_role_coverage("reader-role-id")

        # Results should be identical
        assert second_coverage is not None
        assert first_coverage[0] == second_coverage[0]  # Control plane ops
        assert first_coverage[1] == second_coverage[1]  # Data plane ops

    def test_periodic_refresh_uses_current_cache_data(self, sample_roles, sample_operations):
        """Verify periodic refresh uses data from current cache, not stale data."""
        # Setup with sample data
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        # Get role JSONs from cache (simulates what periodic refresh does)
        role_definitions = get_cache_service().cache.get_role_definitions()
        all_ops = get_cache_service().cache.all_operations

        assert len(role_definitions) == len(sample_roles)
        assert len(all_ops) == len(sample_operations)

        # Run precompute with cache data
        get_cache_service().swap_in_memory(precompute_all(role_definitions, all_ops))

        # Verify computed data is correct
        coverage = get_cache_service().get_role_coverage("reader-role-id")
        assert coverage is not None


# =============================================================================
# Invalidation After Data Change Tests
# =============================================================================


class TestInvalidationAfterDataChange:
    """Tests for cache invalidation after data changes."""

    def test_clear_computed_caches_removes_computed_data(self, sample_roles, sample_operations):
        """Verify clear_computed_caches removes computed fields but preserves source data."""
        # Setup with source and computed data
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service().swap_in_memory(
            precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        )

        # Verify computed data exists
        assert get_cache_service().get_role_coverage("reader-role-id") is not None

        # Clear computed caches (uses singleton by default)
        clear_computed_caches()

        # Computed data should be gone
        assert get_cache_service().get_role_coverage("reader-role-id") is None

        # Source data should be preserved
        assert len(get_cache_service().cache.all_operations) == len(sample_operations)
        assert len(get_cache_service().cache.roles_by_id) == len(sample_roles)

    def test_swap_clears_allowing_roles_cache(self, sample_operations):
        """Verify atomic swap clears allowing_roles_cache."""
        # Populate allowing_roles cache
        container = get_cache_service()
        container.set_allowing_roles("roles_allowing_op:test", [])
        assert container.get_allowing_roles("roles_allowing_op:test") is not None

        # Atomic swap with new data
        new_cache = CacheData.create(all_operations=sample_operations)
        container.swap(new_cache)

        # allowing_roles_cache should be cleared (new CacheData = fresh RequestCaches)
        assert get_cache_service().get_allowing_roles("roles_allowing_op:test") is None


# =============================================================================
# End-to-End Integration Tests
# =============================================================================


class TestCacheLifecycleE2E:
    """End-to-end tests for complete cache lifecycle."""

    def test_data_consistency_across_refresh(self, sample_roles, sample_operations):
        """Verify data remains consistent across multiple refreshes."""
        # Initial setup
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        # Multiple refresh cycles
        results = []
        for _ in range(5):
            get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
            coverage = get_cache_service().get_role_coverage("reader-role-id")
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

        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = {r.role_id: {"role_id": r.role_id, "role_json": r.to_dict()} for r in roles}
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        get_cache_service().swap_in_memory(precompute_all(roles, sample_operations))

        reader_coverage = get_cache_service().get_role_coverage("reader-role-id")
        writer_coverage = get_cache_service().get_role_coverage("writer-role")

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
        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = build_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Verify initial state
        assert get_cache_service().get_role_coverage("reader-role-id") is not None

        # Simulate worker calling clear_computed_caches (invalidation)
        clear_computed_caches()

        # Computed data should be cleared
        assert get_cache_service().get_role_coverage("reader-role-id") is None

        # Source data should still exist
        assert len(get_cache_service().cache.all_operations) == len(sample_operations)

        # Simulate worker rebuilding cache
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        # Computed data should be back
        assert get_cache_service().get_role_coverage("reader-role-id") is not None
