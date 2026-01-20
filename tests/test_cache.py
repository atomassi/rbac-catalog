"""Unit tests for cache utility functions.

Tests for the pure functions in azurerbac.cache.build:
- get_matching_operations
- build_operations_prefix_index
"""

import os
import pickle
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache import (
    CacheContainer,
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    PatternCacheKey,
    Plane,
    compute_operations_hash,
    compute_roles_hash,
    get_cache_service,
)
from azurerbac.cache.backends import FileCacheBackend
from azurerbac.cache.build import (
    build_operations_prefix_index,
    get_matching_operations,
)
from azurerbac.core.constants import EventType, RoleStatus


def make_cached_roles_by_id(roles: list[RoleDefinition]) -> dict[str, CachedRole]:
    """Helper to build roles_by_id dict from RoleDefinition list."""
    return {
        r.role_id: CachedRole(
            definition=r,
            status=RoleStatus.ACTIVE,
        )
        for r in roles
    }


# =============================================================================
# get_matching_operations Tests
# =============================================================================


class TestGetMatchingOperations:
    """Tests for get_matching_operations function."""

    def test_matches_wildcard_read_pattern(self, operation_names: set[str]):
        """Test matching */read pattern."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*/read", operation_names, False, cache)

        # Result contains lowered operation names
        assert "microsoft.storage/storageaccounts/read" in result
        assert "microsoft.compute/virtualmachines/read" in result
        assert "microsoft.keyvault/vaults/read" in result
        assert "microsoft.keyvault/vaults/secrets/read" in result
        assert "microsoft.storage/storageaccounts/write" not in result

    def test_matches_provider_wildcard(self, operation_names: set[str]):
        """Test matching Microsoft.Storage/* pattern."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("Microsoft.Storage/*", operation_names, False, cache)

        # Result contains lowered operation names
        assert "microsoft.storage/storageaccounts/read" in result
        assert "microsoft.storage/storageaccounts/write" in result
        assert "microsoft.storage/storageaccounts/delete" in result
        assert "microsoft.compute/virtualmachines/read" not in result

    def test_matches_star_pattern(self, operation_names: set[str]):
        """Test matching * pattern (matches all)."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*", operation_names, False, cache)

        # Result is lowered version of all operation names
        expected = {op.lower() for op in operation_names}
        assert result == expected

    def test_caches_results(self, operation_names: set[str]):
        """Test that results are cached and reused."""
        cache: dict[PatternCacheKey, set[str]] = {}

        # First call populates cache
        result1 = get_matching_operations("*/read", operation_names, False, cache)

        # Verify cache is populated
        assert PatternCacheKey("*/read", False) in cache

        # Second call should return same result from cache
        result2 = get_matching_operations("*/read", operation_names, False, cache)

        assert result1 == result2
        assert result1 is cache[PatternCacheKey("*/read", False)]

    def test_case_insensitive_pattern(self, operation_names: set[str]):
        """Test that pattern matching is case-insensitive for cache key."""
        cache: dict[PatternCacheKey, set[str]] = {}

        result1 = get_matching_operations("*/READ", operation_names, Plane.DATA, cache)
        result2 = get_matching_operations("*/read", operation_names, Plane.DATA, cache)

        # Both should use the same cache key (lowered)
        assert PatternCacheKey("*/read", Plane.DATA) in cache
        assert PatternCacheKey("*/READ", Plane.DATA) not in cache
        assert result1 == result2

    def test_different_cache_keys_separate_results(self, operation_names: set[str]):
        """Test that different cache keys store separate results."""
        cache: dict[PatternCacheKey, set[str]] = {}

        result1 = get_matching_operations("*/read", operation_names, Plane.CONTROL, cache)
        result2 = get_matching_operations("*/read", operation_names, Plane.DATA, cache)

        assert PatternCacheKey("*/read", Plane.CONTROL) in cache
        assert PatternCacheKey("*/read", Plane.DATA) in cache
        # Results should be equal but stored separately
        assert result1 == result2

    def test_empty_operations_set(self):
        """Test with empty operations set."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*/read", set(), Plane.DATA, cache)

        assert result == set()

    def test_no_matches(self, operation_names: set[str]):
        """Test pattern that matches nothing."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations(
            "Microsoft.NonExistent/*", operation_names, Plane.DATA, cache
        )

        assert result == set()


class TestLowerOptimization:
    """Tests ensuring lower optimization works correctly across cache and matching."""

    def test_role_coverage_stores_lowered(self, operation_names: set[str]):
        """Test that role coverage stores operations lowered."""
        from azurerbac.cache.build import precompute_all

        operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/write", is_data_action=False),
            OperationData(
                name="Microsoft.Storage/storageAccounts/blobServices/read", is_data_action=True
            ),
        ]
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "test-role-id",
                    "properties": {
                        "roleName": "Test Role",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["Microsoft.Storage/storageAccounts/read"]}],
                    },
                }
            )
        ]

        cache_data = precompute_all(roles, operations)

        coverage = cache_data.role_coverage.get("test-role-id")
        assert coverage is not None
        assert "microsoft.storage/storageaccounts/read" in coverage.control
        assert "Microsoft.Storage/storageAccounts/read" not in coverage.control

    def test_pattern_match_cache_stores_lowered(self, operation_names: set[str]):
        """Test that pattern match cache stores operations lowered."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations(
            "Microsoft.Storage/*", operation_names, Plane.CONTROL, cache
        )

        # All results should be lowered
        for op in result:
            assert op == op.lower(), f"Operation {op} is not lowered"

        # Cache should contain lowered
        cached = cache.get(PatternCacheKey("microsoft.storage/*", Plane.CONTROL))
        assert cached is not None
        for op in cached:
            assert op == op.lower()

    def test_case_insensitive_lookup_works(self, operation_names: set[str]):
        """Test that lookups work regardless of input case."""
        cache: dict[PatternCacheKey, set[str]] = {}

        # Query with original case
        result1 = get_matching_operations(
            "Microsoft.Storage/*", operation_names, Plane.CONTROL, cache
        )

        # Query with uppercase
        result2 = get_matching_operations(
            "MICROSOFT.STORAGE/*", operation_names, Plane.CONTROL, cache
        )

        # Query with lowered
        result3 = get_matching_operations(
            "microsoft.storage/*", operation_names, Plane.CONTROL, cache
        )

        # All should return the same results
        assert result1 == result2 == result3

    def test_wildcard_pattern_returns_lowered(self, operation_names: set[str]):
        """Test that wildcard * pattern returns all operations lowered."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*", operation_names, Plane.CONTROL, cache)

        # Result should be all lowered
        expected = {op.lower() for op in operation_names}
        assert result == expected

    def test_explicit_operation_lookup_case_insensitive(self):
        """Test that explicit operation names are matched case-insensitively."""
        from azurerbac.cache.build import precompute_all

        operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
        ]
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "role1",
                    "properties": {
                        "roleName": "Role 1",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["microsoft.storage/storageaccounts/READ"]}],
                    },
                }
            )
        ]

        cache_data = precompute_all(roles, operations)

        coverage = cache_data.role_coverage.get("role1")
        assert coverage is not None
        # Should be stored lowered regardless of input case
        assert "microsoft.storage/storageaccounts/read" in coverage.control

    def test_cache_consistency_with_recommend_roles(self, operation_names: set[str]):
        """Test that cached and non-cached paths return same results."""
        from azurerbac.cache.build import precompute_all
        from azurerbac.matching.role_recommender import recommend_roles
        from tests.helpers import clear_computed_caches

        operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/write", is_data_action=False),
            OperationData(name="Microsoft.Compute/virtualMachines/read", is_data_action=False),
        ]
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "storage-reader",
                    "properties": {
                        "roleName": "Storage Reader",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["Microsoft.Storage/*/read"]}],
                    },
                }
            ),
            RoleDefinition.model_validate(
                {
                    "name": "storage-contrib",
                    "properties": {
                        "roleName": "Storage Contributor",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["Microsoft.Storage/*"]}],
                    },
                }
            ),
        ]

        clear_computed_caches()

        # Without cache
        result_no_cache = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read"],
            roles,
            operations,
        )

        # With cache
        get_cache_service().swap_in_memory(precompute_all(roles, operations))
        result_with_cache = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read"],
            roles,
            operations,
        )

        # Same number of results
        assert len(result_no_cache) == len(result_with_cache)

        # Same roles matched
        no_cache_ids = {r.role_id for r in result_no_cache}
        with_cache_ids = {r.role_id for r in result_with_cache}
        assert no_cache_ids == with_cache_ids

        # Same matched operation counts
        for r_no, r_with in zip(
            sorted(result_no_cache, key=lambda x: x.role_id),
            sorted(result_with_cache, key=lambda x: x.role_id),
            strict=True,
        ):
            assert r_no.matched_operations_count == r_with.matched_operations_count

    def test_ops_lowered_to_orig_restores_casing(self):
        """Test that ops_lowered_to_orig correctly restores original operation casing."""
        from azurerbac.cache.build import precompute_all
        from azurerbac.cache.container import CacheContainer

        operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(
                name="Microsoft.Compute/virtualMachines/start/action", is_data_action=False
            ),
            OperationData(name="Microsoft.KeyVault/vaults/secrets/read", is_data_action=True),
        ]
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "test-role",
                    "properties": {
                        "roleName": "Test Role",
                        "type": "BuiltInRole",
                        "permissions": [
                            {"actions": ["Microsoft.Storage/*", "Microsoft.Compute/*"]}
                        ],
                    },
                }
            )
        ]

        cache_data = precompute_all(roles, operations)
        container = CacheContainer()
        container.swap(cache_data)

        # Verify mapping exists and is correct
        mapping = container.get_ops_lowered_to_orig()
        assert (
            mapping["microsoft.storage/storageaccounts/read"]
            == "Microsoft.Storage/storageAccounts/read"
        )
        assert (
            mapping["microsoft.compute/virtualmachines/start/action"]
            == "Microsoft.Compute/virtualMachines/start/action"
        )
        assert (
            mapping["microsoft.keyvault/vaults/secrets/read"]
            == "Microsoft.KeyVault/vaults/secrets/read"
        )

        # Verify restore_operation_casing works
        lowered_ops = [
            "microsoft.storage/storageaccounts/read",
            "microsoft.compute/virtualmachines/start/action",
        ]
        restored = container.restore_operation_casing(lowered_ops)
        assert restored == [
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Compute/virtualMachines/start/action",
        ]

        # Verify unknown operations are returned as-is
        unknown_ops = ["unknown.operation/read"]
        restored_unknown = container.restore_operation_casing(unknown_ops)
        assert restored_unknown == ["unknown.operation/read"]


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

    def test_lowered_prefix_keys(self, operation_names: set[str]):
        """Test that prefix keys are lowered."""
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

    def test_populate_cache_with_operations(self, sample_operations):
        """Test populate_cache_with_operations builds correct indexes."""
        from tests.helpers import populate_cache_with_operations

        cache = CacheContainer()
        populate_cache_with_operations(cache, sample_operations)

        assert cache.get_all_operations() == sample_operations
        assert len(cache.cache.ops_by_name_lower) == len(sample_operations)
        assert "microsoft.storage/storageaccounts/read" in cache.cache.ops_by_name_lower
        assert "microsoft.compute/virtualmachines/read" in cache.cache.ops_by_name_lower

    def test_populate_cache_with_roles(self, sample_roles_db_format):
        """Test populate_cache_with_roles builds correct index."""
        from tests.helpers import populate_cache_with_roles

        cache = CacheContainer()
        populate_cache_with_roles(cache, sample_roles_db_format)

        assert len(cache.cache.roles_by_id) == 2
        assert "role-1" in cache.cache.roles_by_id
        assert "role-2" in cache.cache.roles_by_id
        assert cache.cache.roles_by_id["role-1"].role_name == "Reader"

    def test_populate_operations_preserves_other_data(
        self, sample_operations, sample_roles_db_format
    ):
        """Test populate_cache_with_operations doesn't overwrite other cache data."""
        from tests.helpers import populate_cache_with_operations, populate_cache_with_roles

        cache = CacheContainer()
        populate_cache_with_roles(cache, sample_roles_db_format)
        populate_cache_with_operations(cache, sample_operations)

        # Roles should still be there
        assert len(cache.cache.roles_by_id) == 2
        # Operations should also be there
        assert len(cache.cache.all_operations) == len(sample_operations)

    def test_populate_roles_preserves_other_data(self, sample_operations, sample_roles_db_format):
        """Test populate_cache_with_roles doesn't overwrite other cache data."""
        from tests.helpers import populate_cache_with_operations, populate_cache_with_roles

        cache = CacheContainer()
        populate_cache_with_operations(cache, sample_operations)
        populate_cache_with_roles(cache, sample_roles_db_format)

        # Operations should still be there
        assert len(cache.cache.all_operations) == len(sample_operations)
        # Roles should also be there
        assert len(cache.cache.roles_by_id) == 2

    def test_set_metadata(self):
        """Test set_metadata updates metadata fields."""
        cache = CacheContainer()

        providers = ["Microsoft.Storage", "Microsoft.Compute"]

        cache.set_metadata(
            unique_providers=providers,
        )

        assert cache.cache.unique_providers == providers

    def test_populate_cache_with_events(self):
        """Test populate_cache_with_events caches events."""
        from tests.helpers import populate_cache_with_events

        cache = CacheContainer()

        events = [
            CachedChangeEvent(
                id=1, role_id="role-1", role_name="Role 1", event_type=EventType.CREATED
            ),
            CachedChangeEvent(
                id=2, role_id="role-2", role_name="Role 2", event_type=EventType.UPDATED
            ),
        ]
        populate_cache_with_events(cache, events)

        assert cache.cache.all_change_events == events

    def test_get_role_by_id(self, sample_roles_db_format):
        """Test get_role_by_id returns correct CachedRole."""
        from tests.helpers import populate_cache_with_roles

        cache = CacheContainer()
        populate_cache_with_roles(cache, sample_roles_db_format)

        role = cache.get_role_by_id("role-1")
        assert role is not None
        assert role.role_name == "Reader"
        assert role.definition is not None

        missing = cache.get_role_by_id("nonexistent")
        assert missing is None

    def test_get_all_roles(self, sample_roles_db_format):
        """Test get_all_roles returns RoleDefinition objects."""
        from tests.helpers import populate_cache_with_roles

        cache = CacheContainer()
        populate_cache_with_roles(cache, sample_roles_db_format)

        roles = cache.get_all_roles()
        assert len(roles) == 2
        # Check they are RoleDefinition objects with properties
        assert all(r.properties is not None for r in roles)

    def test_get_change_events(self):
        """Test get_change_events returns cached events."""
        from tests.helpers import populate_cache_with_events

        cache = CacheContainer()
        events = [
            CachedChangeEvent(
                id=1, role_id="role-1", role_name="Role 1", event_type=EventType.CREATED
            )
        ]
        populate_cache_with_events(cache, events)

        assert cache.get_change_events() == events

    def test_get_events_for_role(self):
        """Test get_events_for_role filters by role_id."""
        from tests.helpers import populate_cache_with_events

        cache = CacheContainer()
        events = [
            CachedChangeEvent(
                id=1, role_id="role-1", role_name="Role 1", event_type=EventType.CREATED
            ),
            CachedChangeEvent(
                id=2, role_id="role-2", role_name="Role 2", event_type=EventType.UPDATED
            ),
            CachedChangeEvent(
                id=3, role_id="role-1", role_name="Role 1", event_type=EventType.UPDATED
            ),
        ]
        populate_cache_with_events(cache, events)

        role1_events = cache.get_events_for_role("role-1")
        assert len(role1_events) == 2
        assert all(e.role_id == "role-1" for e in role1_events)

    def test_role_pages_cache(self):
        """Test role pages caching."""
        cache = CacheContainer()

        page_key = "roles:active::name:asc:1:50"
        roles = [{"role_id": "role-1"}]

        cache.set_role_page(page_key, roles)
        assert cache.get_role_page(page_key) == roles
        assert cache.get_role_page("nonexistent") is None

    def test_misc_cache(self):
        """Test misc key-value cache."""
        cache = CacheContainer()

        cache.set("my_key", {"data": "value"})
        assert cache.get("my_key") == {"data": "value"}
        assert cache.get("missing") is None

    def test_swap_clears_misc_cache(self):
        """Test swap clears misc_cache."""
        cache = CacheContainer()
        cache.set("my_key", "value")

        new_cache_data = CacheData()
        cache.swap(new_cache_data)

        assert cache.get("my_key") is None

    def test_swap_clears_role_pages(self):
        """Test swap clears role_pages cache."""
        cache = CacheContainer()
        page_key = "roles:active::name:asc:1:50"
        cache.set_role_page(page_key, [{"role_id": "role-1"}])

        new_cache_data = CacheData()
        cache.swap(new_cache_data)

        assert cache.get_role_page(page_key) is None


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
        mock_role.last_known_json = {
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
        """Test that preload_cache delegates to rebuild_in_memory and warms role pages."""
        from unittest.mock import AsyncMock, MagicMock, patch

        # Create a mock cache service
        mock_service = MagicMock()
        mock_container = MagicMock(spec=CacheContainer)
        mock_container.cache = CacheData()
        mock_service.container = mock_container
        mock_service.rebuild_in_memory = AsyncMock(return_value=True)

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

        with (
            patch("azurerbac.web.services.startup.get_cache_service", return_value=mock_service),
            patch("azurerbac.telemetry.track_startup"),
            patch("azurerbac.telemetry.track_cache_refresh"),
        ):
            from azurerbac.web.services.startup import preload_cache

            await preload_cache(mock_session_local)

        mock_service.rebuild_in_memory.assert_awaited_once_with(mock_session)
        mock_container.set_role_page.assert_called()


class TestCacheContainerReloadLock:
    """Tests for reload_if_needed thread safety."""

    async def test_reload_lock_prevents_concurrent_calls(self) -> None:
        """Verify reload skips if lock is held."""
        from unittest.mock import MagicMock, patch

        from azurerbac.cache import get_cache_service

        get_cache_service().container.loaded_version = "1000"

        # Acquire the async lock manually
        await get_cache_service().container.reload_lock.acquire()

        try:
            # Try to reload - should return False immediately (lock held)
            mock_backend = MagicMock()
            mock_backend.get_version.return_value = "2000"
            with patch("azurerbac.cache.service.CacheService.backend", return_value=mock_backend):
                result = await get_cache_service().reload_if_needed()
            assert result is False
        finally:
            get_cache_service().container.reload_lock.release()

    async def test_reload_releases_lock_on_success(self) -> None:
        """Verify lock is released after successful reload."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from azurerbac.cache import CacheData, CacheMetadata

        service = get_cache_service()
        service.container.loaded_version = "1000"

        mock_op = OperationData(name="op1", isDataAction=False)
        mock_data = CacheData(
            metadata=CacheMetadata(roles_count=1, operations_count=1),
            roles_by_id=make_cached_roles_by_id(
                [
                    RoleDefinition.model_validate(
                        {"name": "role1", "properties": {"roleName": "Role1"}}
                    )
                ]
            ),
            all_operations=[mock_op],
            unique_providers=["Microsoft.Test"],
        )

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "2000"
        mock_backend.load = AsyncMock(return_value=mock_data)

        # Patch the instance's backend
        with patch.object(service, "_backend", mock_backend):
            result = await service.reload_if_needed()

        assert result is True
        # Lock should be released - we can acquire it
        assert not service.container.reload_lock.locked()

    async def test_reload_releases_lock_on_failure(self) -> None:
        """Verify lock is released if reload fails."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from azurerbac.cache import get_cache_service

        service = get_cache_service()
        service.container.loaded_version = "1000"

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "2000"
        mock_backend.load = AsyncMock(return_value=None)

        with patch.object(service, "_backend", mock_backend):
            result = await service.reload_if_needed()

        assert result is False
        # Lock should be released
        assert not service.container.reload_lock.locked()


# =============================================================================
# Test Fixtures for Disk Cache Tests
# =============================================================================


@pytest.fixture
def roles_for_hashing():
    """Sample role data for hash computation tests.

    Uses minimal structure with updatedOn for modification detection tests.
    Returns RoleDefinition objects as required by compute_roles_hash.
    """
    from azurerbac.azure.models import RoleDefinition

    role_dicts = [
        {
            "name": "role-1",
            "properties": {
                "roleName": "Reader",
                "type": "BuiltInRole",
                "updatedOn": "2024-01-01T00:00:00Z",
            },
        },
        {
            "name": "role-2",
            "properties": {
                "roleName": "Contributor",
                "type": "BuiltInRole",
                "updatedOn": "2024-01-02T00:00:00Z",
            },
        },
    ]
    return [RoleDefinition.model_validate(r) for r in role_dicts]


@pytest.fixture
def operations_for_hashing():
    """Sample operation data for hash computation tests."""
    return [
        OperationData(name="Microsoft.Storage/read", isDataAction=False),
        OperationData(name="Microsoft.Compute/write", isDataAction=False),
        OperationData(name="Microsoft.KeyVault/secrets/read", isDataAction=True),
    ]


# =============================================================================
# Tests for CacheMetadata
# =============================================================================


class TestCacheMetadata:
    """Tests for CacheMetadata validation logic."""

    @pytest.mark.parametrize(
        ("meta_kwargs", "check_kwargs", "expected"),
        [
            pytest.param(
                {"roles_count": 10, "operations_count": 100},
                {"roles_count": 10, "operations_count": 100},
                True,
                id="matching_counts_valid",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100},
                {"roles_count": 15, "operations_count": 100},
                False,
                id="roles_count_mismatch",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100},
                {"roles_count": 10, "operations_count": 150},
                False,
                id="operations_count_mismatch",
            ),
            pytest.param(
                {"version": "v1", "roles_count": 10, "operations_count": 100},
                {"roles_count": 10, "operations_count": 100},
                False,
                id="version_mismatch",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100, "roles_hash": "abc123"},
                {"roles_count": 10, "operations_count": 100, "roles_hash": "xyz789"},
                False,
                id="roles_hash_mismatch",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100, "operations_hash": "abc123"},
                {"roles_count": 10, "operations_count": 100, "operations_hash": "xyz789"},
                False,
                id="operations_hash_mismatch",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100},
                {"roles_count": 10, "operations_count": 100, "roles_hash": "any"},
                True,
                id="empty_stored_roles_hash_ignored",
            ),
            pytest.param(
                {"roles_count": 10, "operations_count": 100},
                {"roles_count": 10, "operations_count": 100, "operations_hash": "any"},
                True,
                id="empty_stored_operations_hash_ignored",
            ),
        ],
    )
    def test_is_valid_for(self, meta_kwargs: dict, check_kwargs: dict, expected: bool):
        """Test cache validity under various conditions."""
        meta = CacheMetadata(**meta_kwargs)
        assert meta.is_valid_for(**check_kwargs) is expected


# =============================================================================
# Tests for Hash Computation
# =============================================================================


class TestHashComputation:
    """Tests for hash computation functions."""

    @pytest.mark.parametrize(
        "hash_func",
        [
            pytest.param(compute_roles_hash, id="roles"),
            pytest.param(compute_operations_hash, id="operations"),
        ],
    )
    def test_hash_empty_list(self, hash_func):
        """Empty list produces a valid hash."""
        result = hash_func([])
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
        from azurerbac.azure.models import RoleDefinition

        hash1 = compute_roles_hash(roles_for_hashing)

        # Create a new role with different updatedOn
        modified_role_dict = {
            "name": "role-1",
            "properties": {
                "roleName": "Reader",
                "type": "BuiltInRole",
                "updatedOn": "2024-12-01T00:00:00Z",
            },
        }
        modified_roles = [RoleDefinition.model_validate(modified_role_dict), roles_for_hashing[1]]
        hash2 = compute_roles_hash(modified_roles)

        assert hash1 != hash2

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
            OperationData(name="New/operation", isDataAction=False),
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
        roles_by_id = make_cached_roles_by_id(sample_roles)
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

    async def test_save_and_load_cache(
        self, temp_cache_dir, sample_roles, sample_operations
    ) -> None:
        """Test saving and loading cache from disk."""
        backend = get_cache_service().backend
        metadata = CacheMetadata(
            roles_count=len(sample_roles),
            operations_count=len(sample_operations),
        )
        # Build CachedRole objects from RoleDefinitions
        roles_by_id = make_cached_roles_by_id(sample_roles)
        data = CacheData(
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_operations=sample_operations,
        )

        # Save
        result = await backend.save(data)
        assert result is True
        assert (temp_cache_dir / "app_cache.msgpack").exists()

        # Load
        loaded = await backend.load()
        assert loaded is not None
        assert loaded.metadata.roles_count == len(sample_roles)
        # Compare role_ids
        assert set(loaded.roles_by_id.keys()) == set(roles_by_id.keys())
        # Verify loaded roles are CachedRole objects
        for role_id, cached_role in loaded.roles_by_id.items():
            assert cached_role.role_name == roles_by_id[role_id].role_name

    async def test_load_nonexistent_cache(self, temp_cache_dir) -> None:
        """Loading nonexistent cache returns None."""
        backend = get_cache_service().backend
        result = await backend.load()
        assert result is None

    async def test_delete_cache(self, temp_cache_dir, sample_roles) -> None:
        """Test deleting cache file."""
        backend = get_cache_service().backend
        # Create a cache file with valid data (roles + operations)
        metadata = CacheMetadata(roles_count=len(sample_roles), operations_count=1)
        roles_by_id = make_cached_roles_by_id(sample_roles)
        all_operations = [OperationData(name="test/op", isDataAction=False)]
        data = CacheData(metadata=metadata, roles_by_id=roles_by_id, all_operations=all_operations)
        await backend.save(data)
        assert (temp_cache_dir / "app_cache.msgpack").exists()

        # Delete
        await backend.delete()
        assert not (temp_cache_dir / "app_cache.msgpack").exists()

    async def test_delete_nonexistent_cache(self, temp_cache_dir):
        """Deleting nonexistent cache doesn't raise error."""
        backend = get_cache_service().backend
        # Should not raise
        await backend.delete()

    async def test_load_corrupted_cache(self, temp_cache_dir) -> None:
        """Loading corrupted cache returns None."""
        # Write corrupted data
        cache_file = temp_cache_dir / "app_cache.msgpack"
        cache_file.write_bytes(b"not valid pickle data")

        backend = get_cache_service().backend
        result = await backend.load()
        assert result is None


# =============================================================================
# Tests for Cache Directory
# =============================================================================


class TestCacheDirectory:
    """Tests for cache directory selection."""

    def test_local_cache_dir(self):
        """Local development uses local .cache directory."""
        # Reset backend's cached directory to force recomputation
        backend = get_cache_service().backend
        assert isinstance(backend, FileCacheBackend)
        backend._cache_dir = None  # pyright: ignore[reportPrivateUsage]

        with patch.dict(os.environ, {}, clear=True):
            # Remove WEBSITE_SITE_NAME if present
            os.environ.pop("WEBSITE_SITE_NAME", None)
            cache_dir = backend.cache_dir
            assert ".cache" in str(cache_dir) or "cache" in str(cache_dir)

    def test_azure_cache_dir(self):
        """Azure App Service uses /home/cache directory."""
        # Reset backend's cached directory to force recomputation
        backend = get_cache_service().backend
        assert isinstance(backend, FileCacheBackend)
        backend._cache_dir = None  # pyright: ignore[reportPrivateUsage]

        with (
            patch.dict(os.environ, {"WEBSITE_SITE_NAME": "test-app"}),
            patch("pathlib.Path.mkdir"),
        ):
            cache_dir = backend.cache_dir
            assert "/home/cache" in str(cache_dir)


# =============================================================================
# Tests for Cache Reload Logic
# =============================================================================


class TestCacheReloadLogic:
    """Tests for cache reload detection and handling edge cases."""

    async def test_version_updated_on_failed_reload_incomplete_cache(self) -> None:
        """When reload fails due to incomplete cache, version should still be updated.

        This prevents retry loops when a bad cache file exists on disk.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from azurerbac.cache import get_cache_service

        service = get_cache_service()
        service.container.loaded_version = "1000"

        # Create incomplete cache data (no operations)
        incomplete_cache = CacheData(
            roles_by_id=make_cached_roles_by_id(
                [
                    RoleDefinition.model_validate(
                        {"name": "role1", "properties": {"roleName": "Role1"}}
                    )
                ]
            ),
            all_operations=[],  # Empty - will fail validation
        )

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "2000"
        mock_backend.load = AsyncMock(return_value=incomplete_cache)

        with patch.object(service, "_backend", mock_backend):
            # Reload should fail but update version
            result = await service.reload_if_needed()
            assert result is False
            # Crucially: version should be updated to prevent retry loop
            assert service.container.loaded_version == "2000"

    async def test_version_updated_on_failed_reload_version_mismatch(self) -> None:
        """When reload fails due to version mismatch, version should still be updated."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from azurerbac.cache import get_cache_service

        service = get_cache_service()
        service.container.loaded_version = "1000"

        # Create cache with wrong version
        old_cache = CacheData(
            metadata=CacheMetadata(version="old-version"),
            roles_by_id=make_cached_roles_by_id(
                [
                    RoleDefinition.model_validate(
                        {"name": "role1", "properties": {"roleName": "Role1"}}
                    )
                ]
            ),
            all_operations=[OperationData(name="op1", isDataAction=False)],
        )

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "2000"
        mock_backend.load = AsyncMock(return_value=old_cache)

        with patch.object(service, "_backend", mock_backend):
            result = await service.reload_if_needed()
            assert result is False
            # version should be updated to prevent retry loop
            assert service.container.loaded_version == "2000"

    def test_no_reload_when_version_same(self) -> None:
        """No reload triggered when disk version equals loaded version."""
        from unittest.mock import MagicMock, patch

        from azurerbac.cache import get_cache_service

        service = get_cache_service()
        service.container.loaded_version = "1000"

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "1000"

        with patch.object(service, "_backend", mock_backend):
            result = service.needs_reload()
            assert result is False

    def test_reload_when_version_different(self) -> None:
        """Reload triggered when disk version differs from loaded version."""
        from unittest.mock import MagicMock, patch

        from azurerbac.cache import get_cache_service

        service = get_cache_service()
        service.container.loaded_version = "1000"

        mock_backend = MagicMock()
        mock_backend.get_version.return_value = "2000"  # Different version

        with patch.object(service, "_backend", mock_backend):
            result = service.needs_reload()
            assert result is True  # Should reload when version changes

    async def test_save_incomplete_cache_rejected(self, temp_cache_dir) -> None:
        """backend.save() should reject incomplete cache data."""
        backend = get_cache_service().backend
        # Empty roles
        data = CacheData(
            roles_by_id={},
            all_operations=[OperationData(name="op1", isDataAction=False)],
        )
        result = await backend.save(data)
        assert result is False
        assert not (temp_cache_dir / "app_cache.msgpack").exists()

        # Empty operations
        data = CacheData(
            roles_by_id=make_cached_roles_by_id(
                [
                    RoleDefinition.model_validate(
                        {"name": "role1", "properties": {"roleName": "Role1"}}
                    )
                ]
            ),
            all_operations=[],
        )
        result = await backend.save(data)
        assert result is False
        assert not (temp_cache_dir / "app_cache.msgpack").exists()


# =============================================================================
# Tests for Cache Invalidation Scenarios
# =============================================================================


class TestCacheInvalidation:
    """Tests for cache invalidation scenarios."""

    async def test_cache_valid_after_app_restart(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ) -> None:
        """Cache should be valid after simulated app restart."""
        backend = get_cache_service().backend
        # Initial save
        roles_hash = compute_roles_hash(roles_for_hashing)
        ops_hash = compute_operations_hash(operations_for_hashing)

        metadata = CacheMetadata(
            roles_count=len(roles_for_hashing),
            operations_count=len(operations_for_hashing),
            roles_hash=roles_hash,
            operations_hash=ops_hash,
        )
        roles_by_id = make_cached_roles_by_id(roles_for_hashing)
        data = CacheData(
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_operations=operations_for_hashing,
        )
        await backend.save(data)

        # Simulate restart - load cache and validate
        loaded = await backend.load()
        assert loaded is not None
        assert loaded.metadata.is_valid_for(
            len(roles_for_hashing),
            len(operations_for_hashing),
            roles_hash,
            ops_hash,
        )

    async def test_cache_invalid_after_role_added(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ) -> None:
        """Cache should be invalid after a new role is added."""
        backend = get_cache_service().backend
        metadata = CacheMetadata(
            roles_count=len(roles_for_hashing),
            operations_count=len(operations_for_hashing),
        )
        roles_by_id = make_cached_roles_by_id(roles_for_hashing)
        data = CacheData(
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_operations=operations_for_hashing,
        )
        await backend.save(data)

        loaded = await backend.load()
        assert loaded is not None
        # Simulate a new role being added
        new_roles_count = len(roles_for_hashing) + 1
        assert not loaded.metadata.is_valid_for(new_roles_count, len(operations_for_hashing))

    async def test_cache_invalid_after_operation_removed(
        self, temp_cache_dir, roles_for_hashing, operations_for_hashing
    ) -> None:
        """Cache should be invalid after an operation is removed."""
        backend = get_cache_service().backend
        metadata = CacheMetadata(
            roles_count=len(roles_for_hashing),
            operations_count=len(operations_for_hashing),
        )
        roles_by_id = make_cached_roles_by_id(roles_for_hashing)
        data = CacheData(
            metadata=metadata,
            roles_by_id=roles_by_id,
            all_operations=operations_for_hashing,
        )
        await backend.save(data)

        loaded = await backend.load()
        assert loaded is not None
        # Simulate an operation being removed
        new_ops_count = len(operations_for_hashing) - 1
        assert not loaded.metadata.is_valid_for(len(roles_for_hashing), new_ops_count)


# =============================================================================
# CachedRole Deserialization Edge Cases
# =============================================================================


class TestCachedRoleFromDict:
    """Edge cases for CachedRole.from_dict deserialization."""

    @pytest.fixture
    def base_role_dict(self) -> dict:
        """Base valid CachedRole dict."""
        return {
            "definition": {
                "id": "/test",
                "name": "test-guid",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {"roleName": "Test Role"},
            },
            "status": "active",
            "last_seen_at": "2024-01-15T10:30:00+00:00",
        }

    def test_valid_cached_role(self, base_role_dict: dict):
        """CachedRole.from_dict works with valid data."""
        cached = CachedRole.from_dict(base_role_dict)
        assert cached.role_name == "Test Role"
        assert cached.status == RoleStatus.ACTIVE
        assert cached.last_seen_at is not None

    @pytest.mark.parametrize(
        ("status", "expected_error"),
        [
            pytest.param("invalid_status", ValueError, id="invalid_status"),
            pytest.param("ACTIVE", ValueError, id="uppercase_status"),
        ],
    )
    def test_invalid_status_raises(self, base_role_dict: dict, status: str, expected_error: type):
        """from_dict with invalid status raises appropriate error."""
        base_role_dict["status"] = status
        with pytest.raises(expected_error):
            CachedRole.from_dict(base_role_dict)

    @pytest.mark.parametrize(
        ("missing_key", "data"),
        [
            pytest.param(
                "definition",
                {"status": "active", "last_seen_at": None},
                id="missing_definition",
            ),
            pytest.param(
                "status",
                {"definition": {"name": "test", "properties": {"roleName": "Test"}}},
                id="missing_status",
            ),
        ],
    )
    def test_missing_required_key_raises(self, missing_key: str, data: dict):
        """from_dict with missing required key raises KeyError."""
        with pytest.raises(KeyError):
            CachedRole.from_dict(data)

    @pytest.mark.parametrize(
        ("last_seen_at_value", "expected"),
        [
            pytest.param(None, None, id="null"),
            pytest.param("__MISSING__", None, id="missing_key"),
        ],
    )
    def test_last_seen_at_optional(
        self, base_role_dict: dict, last_seen_at_value: str | None, expected: datetime | None
    ):
        """from_dict handles missing or null last_seen_at."""
        if last_seen_at_value == "__MISSING__":
            del base_role_dict["last_seen_at"]
        else:
            base_role_dict["last_seen_at"] = last_seen_at_value
        cached = CachedRole.from_dict(base_role_dict)
        assert cached.last_seen_at is expected

    def test_last_seen_at_already_datetime(self, base_role_dict: dict):
        """from_dict handles last_seen_at as datetime object."""
        now = datetime.now(UTC)
        base_role_dict["last_seen_at"] = now
        cached = CachedRole.from_dict(base_role_dict)
        assert cached.last_seen_at == now

    def test_deleted_status(self, base_role_dict: dict):
        """from_dict handles deleted status."""
        base_role_dict["status"] = "deleted"
        cached = CachedRole.from_dict(base_role_dict)
        assert cached.status == RoleStatus.DELETED

    def test_to_dict_roundtrip(self, base_role_dict: dict):
        """to_dict -> from_dict roundtrip preserves data."""
        cached = CachedRole.from_dict(base_role_dict)
        exported = cached.to_dict()
        restored = CachedRole.from_dict(exported)

        assert restored.role_id == cached.role_id
        assert restored.role_name == cached.role_name
        assert restored.status == cached.status
