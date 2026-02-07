"""Tests for cache utility functions."""

import pickle
from unittest.mock import MagicMock

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache import (
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    CacheService,
    PatternCacheKey,
    compute_operations_hash,
    compute_roles_hash,
    get_cache_service,
)
from azurerbac.cache.build import (
    _build_operation_to_roles,
    build_operations_prefix_index,
    get_matching_operations,
)
from azurerbac.cache.models import PopularComparison
from azurerbac.core.constants import EventType, RoleStatus
from azurerbac.matching.models import Plane, RoleCoverage


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

    @pytest.mark.parametrize(
        ("pattern", "expected_in", "expected_not_in"),
        [
            pytest.param(
                "*/read",
                [
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.compute/virtualmachines/read",
                    "microsoft.keyvault/vaults/read",
                    "microsoft.keyvault/vaults/secrets/read",
                ],
                ["microsoft.storage/storageaccounts/write"],
                id="wildcard_read",
            ),
            pytest.param(
                "Microsoft.Storage/*",
                [
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                    "microsoft.storage/storageaccounts/delete",
                ],
                ["microsoft.compute/virtualmachines/read"],
                id="provider_wildcard",
            ),
            pytest.param(
                "Microsoft.NonExistent/*",
                [],
                ["microsoft.storage/storageaccounts/read"],
                id="no_matches",
            ),
        ],
    )
    def test_pattern_matching(
        self,
        operation_names: set[str],
        pattern: str,
        expected_in: list[str],
        expected_not_in: list[str],
    ):
        """Test pattern matching with various wildcard patterns."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations(pattern, operation_names, Plane.CONTROL, cache)

        for op in expected_in:
            assert op in result
        for op in expected_not_in:
            assert op not in result

    def test_matches_star_pattern(self, operation_names: set[str]):
        """Test matching * pattern (matches all)."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*", operation_names, Plane.CONTROL, cache)

        expected = {op.lower() for op in operation_names}
        assert result == expected

    def test_caches_results(self, operation_names: set[str]):
        """Test that results are cached and reused."""
        cache: dict[PatternCacheKey, set[str]] = {}

        result1 = get_matching_operations("*/read", operation_names, Plane.CONTROL, cache)
        assert PatternCacheKey("*/read", Plane.CONTROL) in cache

        result2 = get_matching_operations("*/read", operation_names, Plane.CONTROL, cache)
        assert result1 == result2
        assert result1 is cache[PatternCacheKey("*/read", Plane.CONTROL)]

    def test_case_insensitive_pattern(self, operation_names: set[str]):
        """Test that pattern matching is case-insensitive for cache key."""
        cache: dict[PatternCacheKey, set[str]] = {}

        result1 = get_matching_operations("*/READ", operation_names, Plane.DATA, cache)
        result2 = get_matching_operations("*/read", operation_names, Plane.DATA, cache)

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
        assert result1 == result2

    def test_empty_operations_set(self):
        """Test with empty operations set."""
        cache: dict[PatternCacheKey, set[str]] = {}
        result = get_matching_operations("*/read", set(), Plane.DATA, cache)
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
        """Test that recommend_roles works correctly with cached role coverage."""
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

        # Build cache and run recommend_roles
        get_cache_service().swap_in_memory(precompute_all(roles, operations))
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read"],
            roles,
        )

        # Both roles should match
        assert len(result) == 2
        role_ids = {r.role_id for r in result}
        assert role_ids == {"storage-reader", "storage-contrib"}

        # Both should have matched_operations_count of 1
        for r in result:
            assert r.matched_operations_count == 1

    def test_ops_lowered_to_orig_restores_casing(self):
        """Test that ops_lowered_to_orig correctly restores original operation casing."""
        from azurerbac.cache import CacheService
        from azurerbac.cache.build import precompute_all

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
        service = CacheService()
        service.swap(cache_data)

        # Verify mapping exists and is correct via cache property
        mapping = service.cache.ops_lowered_to_orig
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
        restored = service.restore_operation_casing(lowered_ops)
        assert restored == [
            "Microsoft.Storage/storageAccounts/read",
            "Microsoft.Compute/virtualMachines/start/action",
        ]

        # Verify unknown operations are returned as-is
        unknown_ops = ["unknown.operation/read"]
        restored_unknown = service.restore_operation_casing(unknown_ops)
        assert restored_unknown == ["unknown.operation/read"]


# =============================================================================
# build_operations_prefix_index Tests
# =============================================================================


class TestBuildOperationsPrefixIndex:
    """Tests for build_operations_prefix_index function."""

    def test_builds_correct_index(self, operation_names: set[str]):
        """Test that index groups operations by provider prefix."""
        cache: dict[Plane, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(operation_names, Plane.CONTROL, cache)

        assert "microsoft.storage/" in result
        assert "microsoft.compute/" in result
        assert "microsoft.keyvault/" in result

        # Check Storage operations
        storage_ops = result["microsoft.storage/"]
        assert "Microsoft.Storage/storageAccounts/read" in storage_ops
        assert "Microsoft.Storage/storageAccounts/write" in storage_ops
        assert len(storage_ops) == 4  # read, write, delete, listKeys

    def test_caches_results(self, operation_names: set[str]):
        """Test that results are cached with separate keys per plane."""
        cache: dict[Plane, dict[str, set[str]]] = {}

        result1 = build_operations_prefix_index(operation_names, Plane.CONTROL, cache)
        assert Plane.CONTROL in cache

        result2 = build_operations_prefix_index(operation_names, Plane.CONTROL, cache)
        assert result1 is result2

        # Different plane creates separate cache entry
        build_operations_prefix_index(operation_names, Plane.DATA, cache)
        assert Plane.DATA in cache

    @pytest.mark.parametrize(
        ("ops", "expected_result"),
        [
            pytest.param(set(), {}, id="empty_set"),
            pytest.param(
                {"SimpleOperation", "Microsoft.Storage/read"},
                {"microsoft.storage/": {"Microsoft.Storage/read"}},
                id="ignores_no_slash_ops",
            ),
        ],
    )
    def test_edge_cases(self, ops: set[str], expected_result: dict[str, set[str]]):
        """Test edge cases: empty set and operations without slash."""
        cache: dict[Plane, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(ops, Plane.CONTROL, cache)
        assert result == expected_result

    def test_lowered_prefix_keys(self, operation_names: set[str]):
        """Test that prefix keys are lowered."""
        cache: dict[Plane, dict[str, set[str]]] = {}
        result = build_operations_prefix_index(operation_names, Plane.CONTROL, cache)

        for key in result:
            assert key == key.lower()
            assert key.endswith("/")


# =============================================================================
# _build_operation_to_roles Tests
# =============================================================================


class TestBuildOperationToRoles:
    """Tests for _build_operation_to_roles function."""

    def test_builds_inverted_index_from_coverage(self):
        """Test that inverted index maps operations to role IDs."""
        role_coverage = {
            "role-1": RoleCoverage(
                control={"microsoft.storage/storageaccounts/read"},
                data={"microsoft.storage/storageaccounts/blobservices/containers/blobs/read"},
            ),
            "role-2": RoleCoverage(
                control={
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.compute/virtualmachines/read",
                },
                data=set(),
            ),
        }

        index = _build_operation_to_roles(role_coverage)

        # Storage read should be granted by both roles
        assert "microsoft.storage/storageaccounts/read" in index
        assert set(index["microsoft.storage/storageaccounts/read"]) == {"role-1", "role-2"}

        # Compute read should only be role-2
        assert "microsoft.compute/virtualmachines/read" in index
        assert index["microsoft.compute/virtualmachines/read"] == ["role-2"]

        # Blob read is data action, only role-1
        blob_read_op = "microsoft.storage/storageaccounts/blobservices/containers/blobs/read"
        assert blob_read_op in index
        assert index[blob_read_op] == ["role-1"]

    def test_empty_coverage_returns_empty_index(self):
        """Test that empty role coverage returns empty index."""
        index = _build_operation_to_roles({})
        assert index == {}

    def test_roles_with_no_operations(self):
        """Test roles with empty control and data sets."""
        role_coverage = {
            "empty-role": RoleCoverage(control=set(), data=set()),
        }

        index = _build_operation_to_roles(role_coverage)
        assert index == {}

    def test_preserves_all_role_ids_for_operation(self):
        """Test that all role IDs are preserved when multiple roles grant same operation."""
        role_coverage = {
            "role-a": RoleCoverage(control={"microsoft.resources/subscriptions/read"}, data=set()),
            "role-b": RoleCoverage(control={"microsoft.resources/subscriptions/read"}, data=set()),
            "role-c": RoleCoverage(control={"microsoft.resources/subscriptions/read"}, data=set()),
        }

        index = _build_operation_to_roles(role_coverage)

        sub_read_op = "microsoft.resources/subscriptions/read"
        assert sub_read_op in index
        assert len(index[sub_read_op]) == 3
        assert set(index[sub_read_op]) == {"role-a", "role-b", "role-c"}

    def test_control_and_data_operations_in_same_index(self):
        """Test that control plane and data plane operations go to the same index."""
        role_coverage = {
            "mixed-role": RoleCoverage(
                control={"microsoft.storage/storageaccounts/write"},
                data={"microsoft.storage/storageaccounts/blobservices/containers/blobs/write"},
            ),
        }

        index = _build_operation_to_roles(role_coverage)

        # Both ops should be in the same index
        control_op = "microsoft.storage/storageaccounts/write"
        data_op = "microsoft.storage/storageaccounts/blobservices/containers/blobs/write"

        assert control_op in index
        assert index[control_op] == ["mixed-role"]

        assert data_op in index
        assert index[data_op] == ["mixed-role"]


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

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        assert cache.get_all_operations() == sample_operations
        assert len(cache.cache.ops_by_name_lower) == len(sample_operations)
        assert "microsoft.storage/storageaccounts/read" in cache.cache.ops_by_name_lower
        assert "microsoft.compute/virtualmachines/read" in cache.cache.ops_by_name_lower

    def test_populate_cache_with_roles(self, sample_roles_db_format):
        """Test populate_cache_with_roles builds correct index."""
        from tests.helpers import populate_cache_with_roles

        cache = CacheService()
        populate_cache_with_roles(cache, sample_roles_db_format)

        assert len(cache.cache.roles_by_id) == 2
        assert "role-1" in cache.cache.roles_by_id
        assert "role-2" in cache.cache.roles_by_id
        assert cache.cache.roles_by_id["role-1"].role_name == "Reader"

    def test_populate_preserves_other_data(self, sample_operations, sample_roles_db_format):
        """Test populating operations/roles doesn't overwrite each other."""
        from tests.helpers import populate_cache_with_operations, populate_cache_with_roles

        cache = CacheService()
        populate_cache_with_roles(cache, sample_roles_db_format)
        populate_cache_with_operations(cache, sample_operations)

        # Both should be present
        assert len(cache.cache.roles_by_id) == 2
        assert len(cache.cache.all_operations) == len(sample_operations)

        # Test reverse order too
        cache2 = CacheService()
        populate_cache_with_operations(cache2, sample_operations)
        populate_cache_with_roles(cache2, sample_roles_db_format)

        assert len(cache2.cache.all_operations) == len(sample_operations)
        assert len(cache2.cache.roles_by_id) == 2

    def test_set_metadata(self):
        """Test set_metadata updates metadata fields."""
        cache = CacheService()
        providers = ["Microsoft.Storage", "Microsoft.Compute"]

        cache.set_metadata(unique_providers=providers)
        assert cache.cache.unique_providers == providers

    def test_populate_cache_with_events(self):
        """Test populate_cache_with_events caches events."""
        from tests.helpers import populate_cache_with_events

        cache = CacheService()
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
        """Test get_role_by_id returns correct CachedRole or None."""
        from tests.helpers import populate_cache_with_roles

        cache = CacheService()
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

        cache = CacheService()
        populate_cache_with_roles(cache, sample_roles_db_format)

        roles = cache.get_all_roles()
        assert len(roles) == 2
        assert all(r.properties is not None for r in roles)

    def test_get_change_events(self):
        """Test get_change_events returns cached events."""
        from tests.helpers import populate_cache_with_events

        cache = CacheService()
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

        cache = CacheService()
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

    @pytest.mark.parametrize(
        ("cache_type", "set_method", "get_method", "key", "value"),
        [
            pytest.param(
                "role_pages",
                "set_role_page",
                "get_role_page",
                "roles:active::name:asc:1:50",
                [{"role_id": "role-1"}],
                id="role_pages",
            ),
            pytest.param(
                "role_pages_count",
                "set_role_page",
                "get_role_page",
                "roles_count:active",
                500,
                id="role_pages_count",
            ),
            pytest.param(
                "operation_pages",
                "set_operation_page",
                "get_operation_page",
                "ops:all:name:asc:1:25",
                [{"name": "Microsoft.Storage/read"}],
                id="operation_pages",
            ),
            pytest.param(
                "operation_pages_count",
                "set_operation_page",
                "get_operation_page",
                "ops_count:all",
                21000,
                id="operation_pages_count",
            ),
            pytest.param(
                "allowing_roles",
                "set_allowing_roles",
                "get_allowing_roles",
                "my_key",
                [],
                id="allowing_roles",
            ),
        ],
    )
    def test_request_caches(
        self, cache_type: str, set_method: str, get_method: str, key: str, value
    ):
        """Test LRU request caches (role_pages, operation_pages, allowing_roles)."""
        cache = CacheService()

        getattr(cache, set_method)(key, value)
        assert getattr(cache, get_method)(key) == value
        assert getattr(cache, get_method)("nonexistent") is None

    @pytest.mark.parametrize(
        ("set_method", "get_method", "key"),
        [
            pytest.param("set_role_page", "get_role_page", "roles:page:1", id="role_pages"),
            pytest.param("set_operation_page", "get_operation_page", "ops:page:1", id="op_pages"),
            pytest.param("set_allowing_roles", "get_allowing_roles", "key1", id="allowing_roles"),
            pytest.param("set_filtered_events", "get_filtered_events", "7:all", id="filtered"),
        ],
    )
    def test_swap_clears_request_caches(self, set_method: str, get_method: str, key: str):
        """Test swap clears all request caches."""
        cache = CacheService()
        getattr(cache, set_method)(key, [])

        cache.swap(CacheData())

        assert getattr(cache, get_method)(key) is None

    def test_reset_clears_all_request_caches(self):
        """Test reset clears all RequestCaches (role_pages, operation_pages, etc)."""
        cache = CacheService()

        # Populate all request caches
        cache.set_role_page("roles:page:1", [{"id": "r1"}])
        cache.set_operation_page("ops:page:1", [{"name": "op1"}])
        cache.set_allowing_roles("key1", [])  # type: ignore[arg-type]
        cache.set_filtered_events("7:all", [])  # type: ignore[arg-type]

        cache.reset()

        # All should be cleared
        assert cache.get_role_page("roles:page:1") is None
        assert cache.get_operation_page("ops:page:1") is None
        assert cache.get_allowing_roles("key1") is None
        assert cache.get_filtered_events("7:all") is None


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
        """Test that preload_cache delegates to rebuild_in_memory."""
        from unittest.mock import AsyncMock, MagicMock, patch

        # Create a mock cache service
        mock_service = MagicMock()
        mock_service.cache = CacheData()
        mock_service.rebuild_in_memory = AsyncMock(return_value=True)

        # Mock database session
        mock_session = AsyncMock()

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


# =============================================================================
# Test Fixtures for Hash Computation Tests
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
        data = CacheData.create(
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
# Integration Tests (consolidated from test_cache_e2e.py)
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety and race condition handling."""

    def test_readers_see_consistent_data_during_swap(self, sample_roles, sample_operations):
        """Readers always see consistent data even during cache swap."""
        import threading

        from azurerbac.cache import precompute_all
        from tests.helpers import populate_cache_with_operations

        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = make_cached_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )
        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))

        read_results: list[tuple[int, int, int]] = []
        errors: list[tuple[int, str]] = []

        def reader(reader_id: int) -> None:
            try:
                for _ in range(100):
                    cache = get_cache_service().cache
                    ops_count = len(cache.all_operations)
                    roles_count = len(cache.roles_by_id)
                    read_results.append((reader_id, ops_count, roles_count))
            except Exception as e:
                errors.append((reader_id, str(e)))

        def writer() -> None:
            for _ in range(50):
                new_cache = CacheData.create(
                    all_operations=sample_operations,
                    roles_by_id=roles_by_id,
                )
                get_cache_service().swap(new_cache)

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        threads.append(threading.Thread(target=writer))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        for _, ops_count, roles_count in read_results:
            assert ops_count == len(sample_operations) or ops_count == 0
            assert roles_count == len(sample_roles) or roles_count == 0


class TestDataConsistency:
    """Tests for data consistency across cache operations."""

    def test_role_coverage_matches_role_data(self, sample_roles, sample_operations):
        """Role coverage is computed for all roles in cache."""
        from azurerbac.cache import precompute_all

        roles_by_id = make_cached_roles_by_id(sample_roles)
        cache_data = precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        for role_id in cache_data.roles_by_id:
            assert role_id in cache_data.role_coverage

    def test_role_net_permissions_matches_role_data(self, sample_roles, sample_operations):
        """Role net permissions is computed for all roles in cache."""
        from azurerbac.cache import precompute_all

        roles_by_id = make_cached_roles_by_id(sample_roles)
        cache_data = precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        for role_id in cache_data.roles_by_id:
            assert role_id in cache_data.role_net_permissions

    def test_unique_providers_extracted_correctly(self, sample_roles, sample_operations):
        """unique_providers contains all providers from operations."""
        from azurerbac.cache import precompute_all

        roles_by_id = make_cached_roles_by_id(sample_roles)
        cache_data = precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        expected_providers = {
            op.provider_display_name for op in sample_operations if op.provider_display_name
        }
        assert set(cache_data.unique_providers) == expected_providers


class TestCacheLifecycle:
    """Tests for cache lifecycle and refresh flows."""

    def test_periodic_refresh_recomputes_caches(self, sample_roles, sample_operations):
        """Verify periodic refresh recomputes all caches."""
        from azurerbac.cache import precompute_all
        from tests.helpers import populate_cache_with_operations

        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = make_cached_roles_by_id(sample_roles)
        get_cache_service()._cache = CacheData.create(
            all_operations=sample_operations,
            roles_by_id=roles_by_id,
            ops_by_name_lower=get_cache_service().cache.ops_by_name_lower,
            ops_by_prefix=get_cache_service().cache.ops_by_prefix,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        first_coverage = get_cache_service().get_role_coverage("reader-role-id")
        assert first_coverage is not None

        get_cache_service().swap_in_memory(precompute_all(sample_roles, sample_operations))
        second_coverage = get_cache_service().get_role_coverage("reader-role-id")

        assert second_coverage is not None
        assert first_coverage[0] == second_coverage[0]
        assert first_coverage[1] == second_coverage[1]

    def test_data_consistency_across_multiple_refreshes(self, sample_roles, sample_operations):
        """Verify data remains consistent across multiple refreshes."""
        from azurerbac.cache import precompute_all
        from tests.helpers import populate_cache_with_operations

        populate_cache_with_operations(get_cache_service(), sample_operations)
        roles_by_id = make_cached_roles_by_id(sample_roles)
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
            assert coverage is not None
            results.append((len(coverage[0]), len(coverage[1])))

        assert len(set(results)) == 1

    def test_reset_clears_memory_cache(self, sample_roles, sample_operations):
        """reset() clears all in-memory caches."""
        from azurerbac.cache import precompute_all

        roles_by_id = make_cached_roles_by_id(sample_roles)
        cache_data = precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)
        get_cache_service().swap(cache_data)
        get_cache_service().set_role_page("page1", [{"test": True}])
        get_cache_service().set_allowing_roles("key1", [])

        assert len(get_cache_service().cache.all_operations) > 0
        assert get_cache_service().get_role_page("page1") is not None

        get_cache_service().reset()

        assert len(get_cache_service().cache.all_operations) == 0
        assert get_cache_service().get_role_page("page1") is None


class TestSeedPopularComparisons:
    """Tests for _seed_popular_comparisons warming the LRU cache at startup."""


class TestRebuildInMemory:
    """Tests for CacheService.rebuild_in_memory failure paths."""

    async def test_returns_false_when_lock_held(self):
        """rebuild_in_memory returns False when rebuild lock is already held."""

        from azurerbac.cache.service import _REBUILD_LOCK

        service = get_cache_service()
        await _REBUILD_LOCK.acquire()
        try:
            result = await service.rebuild_in_memory(MagicMock())
            assert result is False
        finally:
            _REBUILD_LOCK.release()

    async def test_returns_false_on_build_exception(self):
        """rebuild_in_memory returns False when build_from_db raises."""
        from unittest.mock import AsyncMock, patch

        from azurerbac.cache.service import CacheService

        with patch.object(
            CacheService, "build_from_db", new=AsyncMock(side_effect=RuntimeError("DB down"))
        ):
            service = get_cache_service()
            result = await service.rebuild_in_memory(MagicMock())
        assert result is False

    def test_seeds_comparisons_for_popular_pairs(self, sample_roles, sample_operations):
        """Popular pairs should be pre-computed and ready in the LRU cache."""
        from azurerbac.cache import precompute_all

        roles_by_id = make_cached_roles_by_id(sample_roles)
        cache_data = precompute_all(sample_roles, sample_operations, roles_by_id=roles_by_id)

        # Inject popular comparisons referencing our test roles
        r_ids = list(roles_by_id.keys())
        if len(r_ids) >= 2:
            from dataclasses import replace

            popular = [
                PopularComparison(
                    role_a_id=r_ids[0],
                    role_a_name=roles_by_id[r_ids[0]].role_name,
                    role_b_id=r_ids[1],
                    role_b_name=roles_by_id[r_ids[1]].role_name,
                    category="Test",
                )
            ]
            cache_data = replace(
                cache_data,
                content=replace(cache_data.content, popular_comparisons=popular),
            )

        service = get_cache_service()
        service.swap(cache_data)

        # Before seeding, the comparisons LRU should be empty
        cache_key = f"{r_ids[0]}:{r_ids[1]}"
        assert service.get_comparison(cache_key) is None

        # Seed and verify the comparison is now cached
        service._seed_popular_comparisons()
        result = service.get_comparison(cache_key)
        assert result is not None
        assert result.role_a.role_id == r_ids[0]
        assert result.role_b.role_id == r_ids[1]

    def test_seed_skips_missing_roles(self):
        """Pairs referencing missing roles should be silently skipped."""
        service = get_cache_service()
        service.reset()

        # Inject a popular pair pointing at nonexistent roles
        from dataclasses import replace

        popular = [
            PopularComparison(
                role_a_id="missing-a",
                role_a_name="Missing A",
                role_b_id="missing-b",
                role_b_name="Missing B",
                category="Test",
            )
        ]
        service._cache = replace(
            service._cache,
            content=replace(service._cache.content, popular_comparisons=popular),
        )

        # Should not raise
        service._seed_popular_comparisons()
        assert service.get_comparison("missing-a:missing-b") is None

    def test_seed_noop_when_no_popular_comparisons(self):
        """Seeding with no popular comparisons should be a no-op."""
        service = get_cache_service()
        service.reset()
        service._seed_popular_comparisons()  # should not raise


# =============================================================================
# sitemap_url Tests
# =============================================================================


class TestSitemapUrl:
    """Tests for the sitemap_url utility function."""

    def test_generates_valid_xml(self):
        from azurerbac.cache.models import sitemap_url

        result = sitemap_url("https://example.com/roles", "2026-01-15", "weekly", 0.8)
        assert "<loc>https://example.com/roles</loc>" in result
        assert "<lastmod>2026-01-15</lastmod>" in result
        assert "<changefreq>weekly</changefreq>" in result
        assert "<priority>0.8</priority>" in result

    def test_uses_defaults(self):
        from azurerbac.cache.models import sitemap_url

        result = sitemap_url("https://example.com", "2026-01-01")
        assert "<changefreq>weekly</changefreq>" in result
        assert "<priority>0.5</priority>" in result


# =============================================================================
# Sitemap.build Tests
# =============================================================================


class TestSitemapBuild:
    """Tests for Sitemap.build classmethod."""

    def test_build_contains_static_pages(self):
        from azurerbac.cache.models import Sitemap

        sitemap = Sitemap.build(
            roles_by_id={},
            all_operations=[],
            site_url="https://test.dev",
        )
        assert '<?xml version="1.0"' in sitemap.content
        assert "<urlset" in sitemap.content
        assert "https://test.dev/roles" in sitemap.content
        assert "https://test.dev/operations" in sitemap.content
        assert "https://test.dev/compare" in sitemap.content
        assert "https://test.dev/recommend" in sitemap.content
        assert "https://test.dev/analytics" in sitemap.content
        assert "https://test.dev/about" in sitemap.content

    def test_build_includes_role_urls(self):
        from azurerbac.cache.models import Sitemap

        role = CachedRole(
            definition=RoleDefinition(
                name="test-guid",
                id="/providers/Microsoft.Authorization/roleDefinitions/test-guid",
                properties={
                    "roleName": "Test Role",
                    "description": "A test",
                    "updatedOn": "2026-01-10T00:00:00+00:00",
                },
            ),
            status=RoleStatus.ACTIVE,
        )
        sitemap = Sitemap.build(
            roles_by_id={"test-guid": role},
            all_operations=[],
            site_url="https://test.dev",
        )
        assert "https://test.dev/roles/test-guid/test-role" in sitemap.content
        assert "2026-01-10" in sitemap.content

    def test_build_includes_operation_urls(self):
        from azurerbac.cache.models import Sitemap

        op = OperationData(
            name="Microsoft.Compute/virtualMachines/read",
            is_data_action=False,
        )
        sitemap = Sitemap.build(
            roles_by_id={},
            all_operations=[op],
            site_url="https://test.dev",
        )
        assert "Microsoft.Compute%2FvirtualMachines%2Fread" in sitemap.content

    def test_build_includes_popular_compare_pairs(self):
        """Popular comparison URLs appear when both role IDs exist in cache."""

        from azurerbac.cache.models import Sitemap
        from azurerbac.core.constants import POPULAR_COMPARE_PAIRS

        # Use the first popular pair from constants
        id_a, id_b, _cat = POPULAR_COMPARE_PAIRS[0]
        roles_by_id = {}
        for rid in (id_a, id_b):
            roles_by_id[rid] = CachedRole(
                definition=RoleDefinition(
                    name=rid,
                    id=f"/providers/Microsoft.Authorization/roleDefinitions/{rid}",
                    properties={
                        "roleName": f"Role {rid[:8]}",
                        "description": "test",
                        "updatedOn": "2026-01-01T00:00:00+00:00",
                    },
                ),
                status=RoleStatus.ACTIVE,
            )

        sitemap = Sitemap.build(
            roles_by_id=roles_by_id,
            all_operations=[],
            site_url="https://test.dev",
        )
        assert f"/compare/{id_a}/{id_b}" in sitemap.content

    def test_build_records_built_at(self):
        import datetime as dt

        from azurerbac.cache.models import Sitemap

        sitemap = Sitemap.build(
            roles_by_id={},
            all_operations=[],
            site_url="https://test.dev",
        )
        assert isinstance(sitemap.built_at, dt.datetime)
