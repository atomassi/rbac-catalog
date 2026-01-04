"""Unit tests for cache utility functions.

Tests for the pure functions in azurerbac.cache.utils:
- get_matching_operations
- build_operations_prefix_index
"""

import os
import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from azurerbac.cache import (
    AppCache,
    CacheData,
    CacheMetadata,
    compute_operations_hash,
    compute_roles_hash,
    delete_cache_file,
    get_cache_dir,
    load_cache_from_disk,
    save_cache_to_disk,
)
from azurerbac.cache.utils import (
    build_operations_prefix_index,
    get_matching_operations,
)

# =============================================================================
# get_matching_operations Tests
# =============================================================================


class TestGetMatchingOperations:
    """Tests for get_matching_operations function."""

    def test_matches_wildcard_read_pattern(self, operation_names: set[str]):
        """Test matching */read pattern."""
        cache: dict[tuple[str, int], set[str]] = {}
        result = get_matching_operations("*/read", operation_names, 1, cache)

        assert "Microsoft.Storage/storageAccounts/read" in result
        assert "Microsoft.Compute/virtualMachines/read" in result
        assert "Microsoft.KeyVault/vaults/read" in result
        assert "Microsoft.KeyVault/vaults/secrets/read" in result
        assert "Microsoft.Storage/storageAccounts/write" not in result

    def test_matches_provider_wildcard(self, operation_names: set[str]):
        """Test matching Microsoft.Storage/* pattern."""
        cache: dict[tuple[str, int], set[str]] = {}
        result = get_matching_operations("Microsoft.Storage/*", operation_names, 1, cache)

        assert "Microsoft.Storage/storageAccounts/read" in result
        assert "Microsoft.Storage/storageAccounts/write" in result
        assert "Microsoft.Storage/storageAccounts/delete" in result
        assert "Microsoft.Compute/virtualMachines/read" not in result

    def test_matches_star_pattern(self, operation_names: set[str]):
        """Test matching * pattern (matches all)."""
        cache: dict[tuple[str, int], set[str]] = {}
        result = get_matching_operations("*", operation_names, 1, cache)

        assert result == operation_names

    def test_caches_results(self, operation_names: set[str]):
        """Test that results are cached and reused."""
        cache: dict[tuple[str, int], set[str]] = {}

        # First call populates cache
        result1 = get_matching_operations("*/read", operation_names, 1, cache)

        # Verify cache is populated
        assert ("*/read", 1) in cache

        # Second call should return same result from cache
        result2 = get_matching_operations("*/read", operation_names, 1, cache)

        assert result1 == result2
        assert result1 is cache[("*/read", 1)]

    def test_case_insensitive_pattern(self, operation_names: set[str]):
        """Test that pattern matching is case-insensitive for cache key."""
        cache: dict[tuple[str, int], set[str]] = {}

        result1 = get_matching_operations("*/READ", operation_names, 1, cache)
        result2 = get_matching_operations("*/read", operation_names, 1, cache)

        # Both should use the same cache key (lowercase)
        assert ("*/read", 1) in cache
        assert ("*/READ", 1) not in cache
        assert result1 == result2

    def test_different_cache_keys_separate_results(self, operation_names: set[str]):
        """Test that different cache keys store separate results."""
        cache: dict[tuple[str, int], set[str]] = {}

        result1 = get_matching_operations("*/read", operation_names, 1, cache)
        result2 = get_matching_operations("*/read", operation_names, 2, cache)

        assert ("*/read", 1) in cache
        assert ("*/read", 2) in cache
        # Results should be equal but stored separately
        assert result1 == result2

    def test_empty_operations_set(self):
        """Test with empty operations set."""
        cache: dict[tuple[str, int], set[str]] = {}
        result = get_matching_operations("*/read", set(), 1, cache)

        assert result == set()

    def test_no_matches(self, operation_names: set[str]):
        """Test pattern that matches nothing."""
        cache: dict[tuple[str, int], set[str]] = {}
        result = get_matching_operations("Microsoft.NonExistent/*", operation_names, 1, cache)

        assert result == set()


# =============================================================================
# build_operations_prefix_index Tests
# =============================================================================


class TestBuildOperationsPrefixIndex:
    """Tests for build_operations_prefix_index function."""

    def test_builds_correct_index(self, operation_names: set[str]):
        """Test that index groups operations by provider prefix."""
        cache: dict[int, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(operation_names, 1, cache)

        assert "microsoft.storage/" in result
        assert "microsoft.compute/" in result
        assert "microsoft.keyvault/" in result

        # Check Storage operations
        storage_ops = result["microsoft.storage/"]
        assert "Microsoft.Storage/storageAccounts/read" in storage_ops
        assert "Microsoft.Storage/storageAccounts/write" in storage_ops
        assert len(storage_ops) == 4  # read, write, delete, listKeys

    def test_caches_results(self, operation_names: set[str]):
        """Test that results are cached."""
        cache: dict[int, dict[str, set[str]]] = {}

        result1 = build_operations_prefix_index(operation_names, 1, cache)
        assert 1 in cache

        result2 = build_operations_prefix_index(operation_names, 1, cache)
        assert result1 is result2

    def test_different_cache_keys(self, operation_names: set[str]):
        """Test different cache keys store separate indexes."""
        cache: dict[int, dict[str, set[str]]] = {}

        build_operations_prefix_index(operation_names, 1, cache)
        build_operations_prefix_index(operation_names, 2, cache)

        assert 1 in cache
        assert 2 in cache

    def test_empty_operations_set(self):
        """Test with empty operations set."""
        cache: dict[int, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(set(), 1, cache)

        assert result == {}

    def test_lowercase_prefix_keys(self, operation_names: set[str]):
        """Test that prefix keys are lowercase."""
        cache: dict[int, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(operation_names, 1, cache)

        for key in result:
            assert key == key.lower()
            assert key.endswith("/")

    def test_operations_without_slash_ignored(self):
        """Test that operations without slash are ignored."""
        ops = {"SimpleOperation", "Microsoft.Storage/read"}
        cache: dict[int, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(ops, 1, cache)

        assert "microsoft.storage/" in result
        assert "simpleoperation" not in result
        # Only one prefix should exist
        assert len(result) == 1


# =============================================================================
# Tests for the preload_cache function in services/startup.py
# These tests ensure that cache initialization works correctly at startup.
# =============================================================================


class TestPreloadCache:
    """Tests for the preload_cache startup function.

    Uses fixtures from conftest.py:
    - mock_async_session: Mock async database session
    - mock_session_factory: Mock SessionLocal factory
    - sample_operations: Sample operations for testing
    - sample_roles_db_format: Sample roles in database format
    """

    def test_build_from_operations(self, sample_operations):
        """Test build_from_operations builds correct indexes."""
        cache = AppCache()
        cache.build_from_operations(sample_operations)

        assert cache.get_all_operations() == sample_operations
        assert len(cache.cache.ops_by_name_lower) == len(sample_operations)
        assert "microsoft.storage/storageaccounts/read" in cache.cache.ops_by_name_lower
        assert "microsoft.compute/virtualmachines/read" in cache.cache.ops_by_name_lower

    def test_build_from_roles(self, sample_roles_db_format):
        """Test build_from_roles builds correct index."""
        cache = AppCache()
        cache.build_from_roles(sample_roles_db_format)

        assert len(cache.cache.roles_by_id) == 2
        assert "role-1" in cache.cache.roles_by_id
        assert "role-2" in cache.cache.roles_by_id
        assert cache.cache.roles_by_id["role-1"]["role_name"] == "Reader"

    def test_build_from_operations_preserves_other_data(
        self, sample_operations, sample_roles_db_format
    ):
        """Test build_from_operations doesn't overwrite other cache data."""
        cache = AppCache()
        cache.build_from_roles(sample_roles_db_format)
        cache.build_from_operations(sample_operations)

        # Roles should still be there
        assert len(cache.cache.roles_by_id) == 2
        # Operations should also be there
        assert len(cache.cache.all_operations) == len(sample_operations)

    def test_build_from_roles_preserves_other_data(self, sample_operations, sample_roles_db_format):
        """Test build_from_roles doesn't overwrite other cache data."""
        cache = AppCache()
        cache.build_from_operations(sample_operations)
        cache.build_from_roles(sample_roles_db_format)

        # Operations should still be there
        assert len(cache.cache.all_operations) == len(sample_operations)
        # Roles should also be there
        assert len(cache.cache.roles_by_id) == 2

    def test_set_metadata(self):
        """Test set_metadata updates metadata fields."""
        cache = AppCache()

        ops_for_rec = [{"name": "test", "is_data_action": False}]
        providers = ["Microsoft.Storage", "Microsoft.Compute"]

        cache.set_metadata(
            operations_for_recommender=ops_for_rec,
            unique_providers=providers,
        )

        assert cache.cache.operations_for_recommender == ops_for_rec
        assert cache.cache.unique_providers == providers

    def test_set_change_events(self):
        """Test set_change_events caches events."""
        cache = AppCache()

        events = [
            {"id": 1, "role_id": "role-1", "event_type": "created"},
            {"id": 2, "role_id": "role-2", "event_type": "updated"},
        ]
        cache.set_change_events(events)

        assert cache.cache.all_change_events == events

    def test_get_role_by_id(self, sample_roles_db_format):
        """Test get_role_by_id returns correct role."""
        cache = AppCache()
        cache.build_from_roles(sample_roles_db_format)

        role = cache.get_role_by_id("role-1")
        assert role is not None
        assert role["role_name"] == "Reader"

        missing = cache.get_role_by_id("nonexistent")
        assert missing is None

    def test_get_all_role_jsons(self, sample_roles_db_format):
        """Test get_all_role_jsons returns role JSONs."""
        cache = AppCache()
        cache.build_from_roles(sample_roles_db_format)

        jsons = cache.get_all_role_jsons()
        assert len(jsons) == 2
        # Check they are actual role_json objects
        assert all("properties" in j for j in jsons)

    def test_get_change_events(self):
        """Test get_change_events returns cached events."""
        cache = AppCache()
        events = [{"id": 1, "event_type": "created"}]
        cache.set_change_events(events)

        assert cache.get_change_events() == events

    def test_get_events_for_role(self):
        """Test get_events_for_role filters by role_id."""
        cache = AppCache()
        events = [
            {"id": 1, "role_id": "role-1", "event_type": "created"},
            {"id": 2, "role_id": "role-2", "event_type": "updated"},
            {"id": 3, "role_id": "role-1", "event_type": "modified"},
        ]
        cache.set_change_events(events)

        role1_events = cache.get_events_for_role("role-1")
        assert len(role1_events) == 2
        assert all(e["role_id"] == "role-1" for e in role1_events)

    def test_role_pages_cache(self):
        """Test role pages caching."""
        cache = AppCache()

        page_key = "roles:active::name:asc:1:50"
        roles = [{"role_id": "role-1"}]

        cache.set_role_page(page_key, roles)
        assert cache.get_role_page(page_key) == roles
        assert cache.get_role_page("nonexistent") is None

    def test_misc_cache(self):
        """Test misc key-value cache."""
        cache = AppCache()

        cache.set("my_key", {"data": "value"})
        assert cache.get("my_key") == {"data": "value"}
        assert cache.get("missing") is None

    def test_swap_clears_misc_cache(self):
        """Test swap clears misc_cache."""
        cache = AppCache()
        cache.set("my_key", "value")

        new_cache_data = CacheData()
        cache.swap(new_cache_data)

        assert cache.get("my_key") is None


class TestPreloadCacheIntegration:
    """Integration tests for _preload_cache with mocked database."""

    @pytest.fixture
    def mock_db_results(self):
        """Create mock database query results."""
        # Mock Role objects
        mock_role = MagicMock()
        mock_role.role_id = "test-role-1"
        mock_role.role_name = "Test Reader"
        mock_role.role_type = "BuiltInRole"
        mock_role.status = "active"
        mock_role.updated_on = None
        mock_role.last_seen_at = None
        mock_role.role_json = {
            "name": "test-role-1",
            "properties": {
                "roleName": "Test Reader",
                "permissions": [{"actions": ["*/read"], "notActions": []}],
            },
        }

        # Mock Operation objects
        mock_op = MagicMock()
        mock_op.name = "Microsoft.Storage/storageAccounts/read"
        mock_op.display_name = "Read Storage Account"
        mock_op.description = "Read storage account"
        mock_op.provider_display_name = "Microsoft Storage"
        mock_op.resource_type_display_name = "Storage Accounts"
        mock_op.is_data_action = False

        # Mock RoleScanStatus
        mock_scan = MagicMock()
        mock_scan.scan_timestamp = None

        # Mock RoleHistory
        mock_event = MagicMock()
        mock_event.id = 1
        mock_event.role_id = "test-role-1"
        mock_event.event_type = "created"
        mock_event.scan = None  # scan relationship
        mock_event.azure_updated_on = None
        mock_event.summary = "Role created"
        mock_event.diff_json = {}

        return {
            "roles": [mock_role],
            "operations": [mock_op],
            "scans": [mock_scan],
            "events": [mock_event],
        }

    @pytest.mark.asyncio
    async def test_preload_cache_calls_build_methods(self, mock_db_results):
        """Test that preload_cache delegates to rebuild_cache and warms role pages."""
        from unittest.mock import ANY, AsyncMock, MagicMock, patch

        # Create a mock cache to track method calls
        mock_cache = MagicMock(spec=AppCache)
        mock_cache.cache = CacheData()
        mock_cache._preloaded = False

        # Mock database session
        mock_session = AsyncMock()

        # Set up scalar returns
        mock_session.scalar = AsyncMock(return_value=None)

        # Set up execute returns for different queries
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_db_results["roles"][0]]
        mock_session.execute = AsyncMock(return_value=mock_result)

        mock_session_local = MagicMock()
        mock_session_local.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

        rebuild_cache_mock = AsyncMock(return_value=True)

        with (
            patch("azurerbac.web.services.startup.app_cache", mock_cache),
            patch("azurerbac.cache.refresh.rebuild_cache", rebuild_cache_mock),
            patch("azurerbac.telemetry.track_startup"),
            patch("azurerbac.telemetry.track_cache_refresh"),
        ):
            from azurerbac.web.services.startup import preload_cache

            await preload_cache(mock_session_local)

        rebuild_cache_mock.assert_awaited_once_with(
            mock_session,
            logger_name=ANY,
            update_in_memory=True,
        )
        mock_cache.set_role_page.assert_called()


class TestAppCacheReloadLock:
    """Tests for reload_from_disk_if_needed thread safety."""

    def test_reload_lock_prevents_concurrent_calls(self):
        """Verify reload skips if lock is held."""
        from unittest.mock import patch

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0

        # Acquire the lock manually
        cache._reload_lock.acquire()

        try:
            # Try to reload - should return False immediately
            with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
                result = cache.reload_from_disk_if_needed()
            assert result is False
        finally:
            cache._reload_lock.release()

    def test_reload_releases_lock_on_success(self):
        """Verify lock is released after successful reload."""
        from unittest.mock import patch

        from azurerbac.cache import CacheData, CacheMetadata

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0

        mock_data = CacheData(
            metadata=CacheMetadata(roles_count=1, operations_count=1),
            roles_by_id={"role1": {"role_id": "role1", "role_json": {"properties": {}}}},
            all_operations=[{"name": "op1", "is_data_action": False}],
            operations_for_recommender=[{"name": "op1", "is_data_action": False}],
            unique_providers=["Microsoft.Test"],
        )

        with (
            patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0),
            patch("azurerbac.cache.app_cache.load_cache_from_disk", return_value=mock_data),
        ):
            result = cache.reload_from_disk_if_needed()

        assert result is True
        # Lock should be released - we can acquire it
        assert cache._reload_lock.acquire(blocking=False)
        cache._reload_lock.release()

    def test_reload_releases_lock_on_failure(self):
        """Verify lock is released if reload fails."""
        from unittest.mock import patch

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0

        with (
            patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0),
            patch("azurerbac.cache.app_cache.load_cache_from_disk", return_value=None),
        ):
            result = cache.reload_from_disk_if_needed()

        assert result is False
        # Lock should be released - we can acquire it
        assert cache._reload_lock.acquire(blocking=False)
        cache._reload_lock.release()

    def test_needs_reload_skip_interval_check(self):
        """Verify needs_reload_from_disk can skip interval check."""
        import time
        from unittest.mock import patch

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = time.time()  # Just checked

        with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0):
            # Normal call - should return False (within interval)
            result = cache.needs_reload_from_disk()
            assert result is False

            # Skip interval check - should return True
            result = cache.needs_reload_from_disk(skip_interval_check=True)
            assert result is True


# =============================================================================
# Test Fixtures for Disk Cache Tests
# =============================================================================


@pytest.fixture
def roles_for_hashing():
    """Sample role data for hash computation tests.

    Uses minimal structure with updatedOn for modification detection tests.
    """
    return [
        {
            "name": "role-1",
            "properties": {
                "roleName": "Reader",
                "updatedOn": "2024-01-01T00:00:00Z",
            },
        },
        {
            "name": "role-2",
            "properties": {
                "roleName": "Contributor",
                "updatedOn": "2024-01-02T00:00:00Z",
            },
        },
    ]


@pytest.fixture
def operations_for_hashing():
    """Sample operation data for hash computation tests."""
    return [
        {"name": "Microsoft.Storage/read", "is_data_action": False},
        {"name": "Microsoft.Compute/write", "is_data_action": False},
        {"name": "Microsoft.KeyVault/secrets/read", "is_data_action": True},
    ]


@pytest.fixture
def temp_cache_dir():
    """Create a temporary directory for cache testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# =============================================================================
# Tests for CacheMetadata
# =============================================================================


class TestCacheMetadata:
    """Tests for CacheMetadata validation logic."""

    def test_is_valid_for_matching_counts(self):
        """Cache is valid when counts match."""
        meta = CacheMetadata(roles_count=10, operations_count=100)
        assert meta.is_valid_for(10, 100) is True

    def test_is_valid_for_roles_count_mismatch(self):
        """Cache is invalid when roles count changes."""
        meta = CacheMetadata(roles_count=10, operations_count=100)
        assert meta.is_valid_for(15, 100) is False

    def test_is_valid_for_operations_count_mismatch(self):
        """Cache is invalid when operations count changes."""
        meta = CacheMetadata(roles_count=10, operations_count=100)
        assert meta.is_valid_for(10, 150) is False

    def test_is_valid_for_version_mismatch(self):
        """Cache is invalid when version changes."""
        meta = CacheMetadata(version="v1", roles_count=10, operations_count=100)
        assert meta.is_valid_for(10, 100) is False

    def test_is_valid_for_roles_hash_mismatch(self):
        """Cache is invalid when roles hash changes."""
        meta = CacheMetadata(roles_count=10, operations_count=100, roles_hash="abc123")
        assert meta.is_valid_for(10, 100, roles_hash="xyz789") is False

    def test_is_valid_for_operations_hash_mismatch(self):
        """Cache is invalid when operations hash changes."""
        meta = CacheMetadata(roles_count=10, operations_count=100, operations_hash="abc123")
        assert meta.is_valid_for(10, 100, operations_hash="xyz789") is False

    def test_is_valid_for_empty_hashes_ignored(self):
        """Empty hashes are not checked."""
        meta = CacheMetadata(roles_count=10, operations_count=100)
        # Empty hashes should not affect validation
        assert meta.is_valid_for(10, 100, roles_hash="any") is True
        assert meta.is_valid_for(10, 100, operations_hash="any") is True


# =============================================================================
# Tests for Hash Computation
# =============================================================================


class TestHashComputation:
    """Tests for hash computation functions."""

    def test_compute_roles_hash_empty(self):
        """Empty roles list produces a hash."""
        result = compute_roles_hash([])
        assert isinstance(result, str)
        assert len(result) == 32  # Full MD5 hex digest

    def test_compute_roles_hash_deterministic(self, roles_for_hashing):
        """Same roles produce same hash."""
        hash1 = compute_roles_hash(roles_for_hashing)
        hash2 = compute_roles_hash(roles_for_hashing)
        assert hash1 == hash2

    def test_compute_roles_hash_order_independent(self, roles_for_hashing):
        """Hash is the same regardless of role order."""
        hash1 = compute_roles_hash(roles_for_hashing)
        hash2 = compute_roles_hash(list(reversed(roles_for_hashing)))
        assert hash1 == hash2

    def test_compute_roles_hash_changes_on_update(self, roles_for_hashing):
        """Hash changes when role data changes."""
        hash1 = compute_roles_hash(roles_for_hashing)

        modified_roles = roles_for_hashing.copy()
        modified_roles[0]["properties"]["updatedOn"] = "2024-12-01T00:00:00Z"
        hash2 = compute_roles_hash(modified_roles)

        assert hash1 != hash2

    def test_compute_operations_hash_empty(self):
        """Empty operations list produces a hash."""
        result = compute_operations_hash([])
        assert isinstance(result, str)
        assert len(result) == 32  # Full MD5 hex digest

    def test_compute_operations_hash_deterministic(self, operations_for_hashing):
        """Same operations produce same hash."""
        hash1 = compute_operations_hash(operations_for_hashing)
        hash2 = compute_operations_hash(operations_for_hashing)
        assert hash1 == hash2

    def test_compute_operations_hash_order_independent(self, operations_for_hashing):
        """Hash is the same regardless of operation order."""
        hash1 = compute_operations_hash(operations_for_hashing)
        hash2 = compute_operations_hash(list(reversed(operations_for_hashing)))
        assert hash1 == hash2

    def test_compute_operations_hash_changes_on_new_op(self, operations_for_hashing):
        """Hash changes when operation is added."""
        hash1 = compute_operations_hash(operations_for_hashing)

        modified_ops = [
            *operations_for_hashing,
            {"name": "New/operation", "is_data_action": False},
        ]
        hash2 = compute_operations_hash(modified_ops)

        assert hash1 != hash2


# =============================================================================
# Tests for CacheData
# =============================================================================


class TestCacheData:
    """Tests for CacheData dataclass."""

    def test_serialization(self, sample_roles, sample_operations):
        """Test that cache data can be serialized and deserialized."""
        metadata = CacheMetadata(
            roles_count=len(sample_roles),
            operations_count=len(sample_operations),
        )
        # Convert sample_roles to roles_by_id format
        roles_by_id = {r["name"]: r for r in sample_roles}
        data = CacheData(
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
        )

        # Serialize
        serialized = pickle.dumps(data)
        assert serialized is not None

        # Deserialize
        restored = pickle.loads(serialized)  # noqa: S301 - testing our own serialization
        assert restored.metadata.roles_count == len(sample_roles)
        assert restored.roles_by_id == roles_by_id
        assert restored.all_operations == sample_operations


# =============================================================================
# Tests for Cache File Operations
# =============================================================================


class TestCacheFileOperations:
    """Tests for save/load/delete cache file operations."""

    def test_save_and_load_cache(self, temp_cache_dir, sample_roles, sample_operations):
        """Test saving and loading cache from disk."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            metadata = CacheMetadata(
                roles_count=len(sample_roles),
                operations_count=len(sample_operations),
            )
            roles_by_id = {r["name"]: r for r in sample_roles}
            data = CacheData(
                metadata=metadata,
                roles_by_id=roles_by_id,
                all_operations=sample_operations,
            )

            # Save
            result = save_cache_to_disk(data)
            assert result is True
            assert (temp_cache_dir / "app_cache.msgpack").exists()

            # Load
            loaded = load_cache_from_disk()
            assert loaded is not None
            assert loaded.metadata.roles_count == len(sample_roles)
            assert loaded.roles_by_id == roles_by_id

    def test_load_nonexistent_cache(self, temp_cache_dir):
        """Loading nonexistent cache returns None."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            result = load_cache_from_disk()
            assert result is None

    def test_delete_cache(self, temp_cache_dir, sample_roles):
        """Test deleting cache file."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Create a cache file with valid data (roles + operations)
            metadata = CacheMetadata(roles_count=len(sample_roles), operations_count=1)
            roles_by_id = {r["name"]: r for r in sample_roles}
            all_operations = [{"name": "test/op", "is_data_action": False}]
            data = CacheData(
                metadata=metadata, roles_by_id=roles_by_id, all_operations=all_operations
            )
            save_cache_to_disk(data)
            assert (temp_cache_dir / "app_cache.msgpack").exists()

            # Delete
            delete_cache_file()
            assert not (temp_cache_dir / "app_cache.msgpack").exists()

    def test_delete_nonexistent_cache(self, temp_cache_dir):
        """Deleting nonexistent cache doesn't raise error."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Should not raise
            delete_cache_file()

    def test_load_corrupted_cache(self, temp_cache_dir):
        """Loading corrupted cache returns None."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Write corrupted data
            cache_file = temp_cache_dir / "app_cache.msgpack"
            cache_file.write_bytes(b"not valid pickle data")

            result = load_cache_from_disk()
            assert result is None


# =============================================================================
# Tests for Cache Directory
# =============================================================================


class TestCacheDirectory:
    """Tests for cache directory selection."""

    def test_local_cache_dir(self):
        """Local development uses local .cache directory."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove WEBSITE_SITE_NAME if present
            os.environ.pop("WEBSITE_SITE_NAME", None)
            cache_dir = get_cache_dir()
            assert ".cache" in str(cache_dir) or "cache" in str(cache_dir)

    def test_azure_cache_dir(self):
        """Azure App Service uses /home/cache directory."""
        with (
            patch.dict(os.environ, {"WEBSITE_SITE_NAME": "test-app"}),
            patch("pathlib.Path.mkdir"),
        ):
            cache_dir = get_cache_dir()
            assert "/home/cache" in str(cache_dir)


# =============================================================================
# Tests for Cache Reload Logic
# =============================================================================


class TestCacheReloadLogic:
    """Tests for cache reload detection and handling edge cases."""

    def test_mtime_updated_on_failed_reload_incomplete_cache(self):
        """When reload fails due to incomplete cache, mtime should still be updated.

        This prevents retry loops when a bad cache file exists on disk.
        """

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0  # Allow check

        # Create incomplete cache data (no operations)
        incomplete_cache = CacheData(
            roles_by_id={"role1": {"name": "Role1"}},
            all_operations=[],  # Empty - will fail validation
        )

        with (
            patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0),
            patch("azurerbac.cache.app_cache.load_cache_from_disk", return_value=incomplete_cache),
        ):
            # Reload should fail but update mtime
            result = cache.reload_from_disk_if_needed()
            assert result is False
            # Crucially: mtime should be updated to prevent retry loop
            assert cache._loaded_cache_mtime == 2000.0

    def test_mtime_updated_on_failed_reload_version_mismatch(self):
        """When reload fails due to version mismatch, mtime should still be updated."""
        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0

        # Create cache with wrong version
        old_cache = CacheData(
            metadata=CacheMetadata(version="old-version"),
            roles_by_id={"role1": {"name": "Role1"}},
            all_operations=[{"name": "op1", "is_data_action": False}],
        )

        with (
            patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=2000.0),
            patch("azurerbac.cache.app_cache.load_cache_from_disk", return_value=old_cache),
        ):
            result = cache.reload_from_disk_if_needed()
            assert result is False
            # mtime should be updated to prevent retry loop
            assert cache._loaded_cache_mtime == 2000.0

    def test_no_reload_when_mtime_same(self):
        """No reload triggered when disk mtime equals loaded mtime."""
        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0  # Allow check

        with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=1000.0):
            result = cache.needs_reload_from_disk()
            assert result is False

    def test_no_reload_when_mtime_older(self):
        """No reload triggered when disk mtime is older than loaded mtime."""
        cache = AppCache()
        cache._loaded_cache_mtime = 2000.0
        cache._last_cache_check = 0

        with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=1000.0):
            result = cache.needs_reload_from_disk()
            assert result is False

    def test_normal_check_updates_throttle(self):
        """Normal check should update _last_cache_check."""
        import time

        cache = AppCache()
        cache._loaded_cache_mtime = 1000.0
        cache._last_cache_check = 0  # Very old - allows check

        before = time.time()
        with patch("azurerbac.cache.app_cache.get_cache_file_mtime", return_value=1000.0):
            cache.needs_reload_from_disk()
        after = time.time()

        # Throttle timestamp should be updated to now
        assert before <= cache._last_cache_check <= after

    def test_save_incomplete_cache_rejected(self):
        """save_cache_to_disk should reject incomplete cache data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_path):
                # Empty roles
                data = CacheData(
                    roles_by_id={},
                    all_operations=[{"name": "op1", "is_data_action": False}],
                )
                result = save_cache_to_disk(data)
                assert result is False
                assert not (temp_path / "app_cache.msgpack").exists()

                # Empty operations
                data = CacheData(
                    roles_by_id={"role1": {"name": "Role1"}},
                    all_operations=[],
                )
                result = save_cache_to_disk(data)
                assert result is False
                assert not (temp_path / "app_cache.msgpack").exists()


# =============================================================================
# Tests for Cache Invalidation Scenarios
# =============================================================================


class TestCacheInvalidation:
    """Tests for cache invalidation scenarios."""

    def test_cache_valid_after_app_restart(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ):
        """Cache should be valid after simulated app restart."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            # Initial save
            roles_hash = compute_roles_hash(roles_for_hashing)
            ops_hash = compute_operations_hash(operations_for_hashing)

            metadata = CacheMetadata(
                roles_count=len(roles_for_hashing),
                operations_count=len(operations_for_hashing),
                roles_hash=roles_hash,
                operations_hash=ops_hash,
            )
            roles_by_id = {r["name"]: r for r in roles_for_hashing}
            data = CacheData(
                metadata=metadata,
                roles_by_id=roles_by_id,
                all_operations=operations_for_hashing,
            )
            save_cache_to_disk(data)

            # Simulate restart - load cache and validate
            loaded = load_cache_from_disk()
            assert loaded is not None
            assert loaded.metadata.is_valid_for(
                len(roles_for_hashing),
                len(operations_for_hashing),
                roles_hash,
                ops_hash,
            )

    def test_cache_invalid_after_role_added(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ):
        """Cache should be invalid after a new role is added."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            metadata = CacheMetadata(
                roles_count=len(roles_for_hashing),
                operations_count=len(operations_for_hashing),
            )
            roles_by_id = {r["name"]: r for r in roles_for_hashing}
            data = CacheData(
                metadata=metadata,
                roles_by_id=roles_by_id,
                all_operations=operations_for_hashing,
            )
            save_cache_to_disk(data)

            loaded = load_cache_from_disk()
            # Simulate a new role being added
            new_roles_count = len(roles_for_hashing) + 1
            assert not loaded.metadata.is_valid_for(new_roles_count, len(operations_for_hashing))

    def test_cache_invalid_after_operation_removed(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ):
        """Cache should be invalid after an operation is removed."""
        with patch("azurerbac.cache.persistence.get_cache_dir", return_value=temp_cache_dir):
            metadata = CacheMetadata(
                roles_count=len(roles_for_hashing),
                operations_count=len(operations_for_hashing),
            )
            roles_by_id = {r["name"]: r for r in roles_for_hashing}
            data = CacheData(
                metadata=metadata,
                roles_by_id=roles_by_id,
                all_operations=operations_for_hashing,
            )
            save_cache_to_disk(data)

            loaded = load_cache_from_disk()
            # Simulate an operation being removed
            new_ops_count = len(operations_for_hashing) - 1
            assert not loaded.metadata.is_valid_for(len(roles_for_hashing), new_ops_count)
