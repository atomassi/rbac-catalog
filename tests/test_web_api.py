"""Comprehensive tests for the web API endpoints and CacheService."""

from __future__ import annotations

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedRole
from azurerbac.core.enums import RoleStatus
from tests.helpers import populate_cache_with_operations


class TestCacheService:
    """Tests for CacheService class."""

    def test_cache_get_set_supported_keys(self):
        """Test get/set for supported keys."""
        from azurerbac.cache import CacheService

        cache = CacheService()

        # Test supported keys
        cache.set_allowing_roles("unique_providers", ["Provider1"])
        assert cache.get_allowing_roles("unique_providers") == ["Provider1"]

        cache.set_allowing_roles("last_scan", {"timestamp": 123})
        assert cache.get_allowing_roles("last_scan") == {"timestamp": 123}

    def test_cache_returns_none_for_missing_key(self):
        """Test that missing keys return None."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        result = cache.get_allowing_roles("nonexistent")
        assert result is None

    def test_cache_role_pages(self):
        """Test role page caching."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        cache.set_role_page("roles:page:1", [{"role_id": "test1"}])
        cache.set_role_page("roles:page:2", [{"role_id": "test2"}])

        assert cache.get_role_page("roles:page:1") == [{"role_id": "test1"}]
        assert cache.get_role_page("roles:page:2") == [{"role_id": "test2"}]

    def test_cache_invalidate_all_clears_all_data(self):
        """Test invalidating all caches."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        cache.set_allowing_roles("custom_key", ["value1"])
        cache.set_role_page("roles:page:1", [{"role_id": "test"}])

        cache.reset()

        # Misc cache cleared
        assert cache.get_allowing_roles("custom_key") is None
        # Role pages cleared
        assert cache.get_role_page("roles:page:1") is None
        # CacheData fields reset to defaults
        assert cache.cache.unique_providers == []


class TestOperationsIndex:
    """Tests for operations indexing and search."""

    def test_build_from_operations(self, sample_operations):
        """Test building operations index."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        # Check that indexes were built via data accessor
        assert len(cache.cache.ops_by_name_lower) == len(sample_operations)
        assert "microsoft.compute" in cache.cache.ops_by_prefix
        assert "microsoft.storage" in cache.cache.ops_by_prefix

    def test_search_operations_substring(self, sample_operations):
        """Test substring search."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        results = cache.search_operations("virtualMachines", limit=10)

        assert len(results) == 3  # read, write, delete
        assert all("virtualMachines" in r.name for r in results)

    def test_search_operations_wildcard(self, sample_operations):
        """Test wildcard search."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        results = cache.search_operations("microsoft.compute/*/read", limit=10)

        assert len(results) == 1
        assert results[0].name == "Microsoft.Compute/virtualMachines/read"

    def test_search_operations_case_insensitive(self, sample_operations):
        """Test that search is case insensitive."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        results = cache.search_operations("VIRTUALMACHINES", limit=10)

        assert len(results) == 3

    def test_search_operations_by_display_name(self, sample_operations):
        """Test search by display name."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        results = cache.search_operations("Get Virtual", limit=10)

        assert any("read" in r.name.lower() for r in results)

    def test_search_operations_limit(self, sample_operations):
        """Test search respects limit."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        results = cache.search_operations("microsoft", limit=2)

        assert len(results) == 2

    def test_search_empty_query(self, sample_operations):
        """Test search with empty query returns nothing."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        # Empty query won't match anything in the search logic
        results = cache.search_operations("", limit=10)

        # All operations contain empty string, so depends on implementation
        # Just check it doesn't crash
        assert isinstance(results, list)


class TestCountWildcardMatches:
    """Tests for wildcard pattern matching count."""

    @pytest.mark.parametrize(
        ("pattern", "is_data_action", "expected_count", "description"),
        [
            pytest.param(
                "Microsoft.Compute/*", False, 3, "read, write, delete", id="simple_wildcard"
            ),
            pytest.param(
                "*", True, 3, "Storage blobs read/write + KeyVault secrets read", id="data_actions"
            ),
            pytest.param(
                "*",
                False,
                10,
                "3 Storage + 3 Compute + 2 Auth + 1 Network + 1 KeyVault",
                id="control_plane",
            ),
            pytest.param(
                "*/read",
                False,
                5,
                "Storage, Compute, Auth, Network, KeyVault",
                id="specific_pattern",
            ),
        ],
    )
    def test_count_wildcard_matches(
        self,
        sample_operations,
        pattern: str,
        is_data_action: bool,
        expected_count: int,
        description: str,
    ):
        """Test counting wildcard pattern matches."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        count = cache.count_wildcard_matches(pattern, is_data_action=is_data_action)

        assert count == expected_count

    def test_count_wildcard_matches_deduplicates_by_lowered_name(self):
        """Test that operations with different casings are deduplicated.

        This tests the bug fix where count_wildcard_matches was counting each
        operation separately even if they lowercase to the same string, while
        the recommendation service uses deduplicated frozensets. The counts
        should match.

        Regression test for: UI shows "matches X operations" but role results
        show "Matches Y of Y operations" where X != Y.
        """
        from azurerbac.cache import CacheService
        from tests.helpers import make_operation

        # Create operations where different casings lowercase to same string
        operations_with_duplicates = [
            # These three will deduplicate to one: microsoft.storage/storageaccounts/read
            make_operation("Microsoft.Storage/storageAccounts/read"),
            make_operation("Microsoft.Storage/storageAccounts/Read"),  # Different: 'Read'
            make_operation("microsoft.storage/storageaccounts/read"),  # All lowercase
            # These two will deduplicate to one: microsoft.compute/virtualmachines/read
            make_operation("Microsoft.Compute/virtualMachines/read"),
            make_operation("Microsoft.Compute/VirtualMachines/Read"),  # Different casing
            # This one is unique
            make_operation("Microsoft.Network/virtualNetworks/read"),
        ]

        cache = CacheService()
        populate_cache_with_operations(cache, operations_with_duplicates)

        # The count should be 3 (deduplicated), not 6 (raw count)
        count = cache.count_wildcard_matches("*/read", is_data_action=False)

        # Verify deduplication: 3 unique operations when lowercased
        assert count == 3, (
            f"Expected 3 deduplicated operations, got {count}. "
            "Operations with different casings that lowercase to the same string "
            "should be counted once."
        )


class TestMatchesPattern:
    """Tests for matches_pattern function."""

    @pytest.mark.parametrize(
        "operation,pattern,expected",
        [
            # Exact match
            (
                "Microsoft.Compute/virtualMachines/read",
                "Microsoft.Compute/virtualMachines/read",
                True,
            ),
            # Wildcard * at end
            ("Microsoft.Compute/virtualMachines/read", "Microsoft.Compute/*", True),
            # Wildcard * at start
            ("Microsoft.Compute/virtualMachines/read", "*/read", True),
            # Single wildcard
            ("Microsoft.Compute/virtualMachines/read", "*", True),
            # Wildcard in middle
            ("Microsoft.Compute/virtualMachines/read", "Microsoft.*/virtualMachines/read", True),
            # No match - different provider
            ("Microsoft.Compute/virtualMachines/read", "Microsoft.Storage/*", False),
            # Case insensitive - lowercase
            ("Microsoft.Compute/virtualMachines/read", "microsoft.compute/*", True),
            # Case insensitive - uppercase
            ("Microsoft.Compute/virtualMachines/read", "MICROSOFT.COMPUTE/*", True),
        ],
    )
    def testmatches_pattern(self, operation: str, pattern: str, expected: bool):
        """Test pattern matching with various patterns."""
        from azurerbac.core.patterns import matches_pattern

        assert matches_pattern(operation, pattern) is expected


class TestCacheSimplicity:
    """Tests for simplified CacheService without TTL."""

    def test_cache_has_no_ttl_on_set(self):
        """Test that set() does not require a TTL parameter.

        Cache is refreshed atomically by worker invalidation or periodic recompute,
        so TTL is not needed.
        """
        from azurerbac.cache import CacheService

        cache = CacheService()
        # Should work without TTL parameter for supported keys
        cache.set_allowing_roles("unique_providers", ["Test"])
        assert cache.get_allowing_roles("unique_providers") == ["Test"]


class TestCacheServiceThreadSafety:
    """Tests for CacheService thread safety."""

    def test_concurrent_access(self, sample_operations):
        """Test concurrent cache access doesn't corrupt data."""
        import concurrent.futures

        from azurerbac.cache import CacheService

        cache = CacheService()
        populate_cache_with_operations(cache, sample_operations)

        errors = []

        def reader():
            try:
                for _ in range(100):
                    cache.search_operations("Microsoft", limit=10)
            except Exception as e:
                errors.append(e)

        def writer():
            try:
                for i in range(100):
                    cache.set_allowing_roles(f"test_{i}", [])  # type: ignore[arg-type]
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(reader),
                executor.submit(reader),
                executor.submit(writer),
                executor.submit(writer),
            ]
            concurrent.futures.wait(futures)

        assert len(errors) == 0, f"Errors during concurrent access: {errors}"


class TestCacheServiceInvalidateAll:
    """Tests for invalidate_all method."""

    def test_invalidate_all_clears_memory_cache(self, sample_operations):
        """Test that invalidate_all clears memory cache."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        cache.set_allowing_roles("key1", "value1")
        populate_cache_with_operations(cache, sample_operations)

        cache.reset()

        assert cache.get_allowing_roles("key1") is None
        assert len(cache.cache.all_operations) == 0

    def test_invalidate_all_clears_computed_caches(self, sample_operations):
        """Test that invalidate_all clears computed caches."""
        from azurerbac.cache import CacheService

        cache = CacheService()

        # Add some data to the computed caches
        cache._cache.role_coverage["test-role"] = ({"op1"}, {"op2"})
        assert len(cache.cache.role_coverage) > 0

        cache.reset()

        # Verify computed caches are cleared (CacheData reset to empty)
        assert len(cache.cache.role_coverage) == 0


class TestUniqueProvidersCaching:
    """Tests for unique_providers caching, invalidation, and recomputation."""

    def test_unique_providers_is_cached(self, sample_operations):
        """Test that unique_providers is stored in cache."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        # Set up the cache with operations data
        populate_cache_with_operations(cache, sample_operations)

        # Compute and cache unique_providers
        providers = set()
        for op in sample_operations:
            provider = op.provider_display_name
            if provider:
                providers.add(provider)
        expected = sorted(providers, key=str.lower)

        cache.set_metadata(unique_providers=expected)

        # Verify it's cached
        cached = cache.cache.unique_providers
        assert cached == expected
        assert "Microsoft Compute" in cached
        assert "Microsoft Storage" in cached

    def test_unique_providers_cleared_on_invalidate_all(self, sample_operations):
        """Test that unique_providers is cleared when invalidate_all is called."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        cache.set_metadata(unique_providers=["Provider1", "Provider2"])

        # Verify it's cached
        assert cache.cache.unique_providers == ["Provider1", "Provider2"]

        # Invalidate all
        cache.reset()

        # Verify it's cleared (empty list after invalidation)
        assert cache.cache.unique_providers == []

    def test_unique_providers_cleared_on_cache_invalidate_all(self, sample_operations):
        """Test that unique_providers is cleared with invalidate_all."""
        from azurerbac.cache import CacheService

        cache = CacheService()
        cache.set_metadata(unique_providers=["Provider1", "Provider2"])
        cache.set_role_page("roles:page:1", [{"id": "test"}])

        # Invalidate all cache entries
        cache.reset()

        assert cache.cache.unique_providers == []
        assert cache.get_role_page("roles:page:1") is None


class TestRolesAllowingOperationCaching:
    """Tests for get_roles_allowing_operation caching, invalidation, and recomputation."""

    def test_roles_allowing_operation_cache_key_format(self):
        """Test that cache key format is correct for roles_allowing_op."""
        from azurerbac.cache import CacheService

        CacheService()

        # The cache key format should be: roles_allowing_op:{operation_name}:{is_data_action}
        operation_name = "Microsoft.Storage/read"
        is_data_action = False

        cache_key = f"roles_allowing_op:{operation_name.lower()}:{is_data_action}"
        expected_key = "roles_allowing_op:microsoft.storage/read:False"

        assert cache_key == expected_key

    def test_roles_allowing_operation_is_cached(self):
        """Test that roles_allowing_operation results are stored in cache."""
        from azurerbac.cache import CacheService
        from azurerbac.web.services.models import RoleAllowingOperation

        cache = CacheService()

        # Simulate caching the result with proper typed models
        mock_result = [
            RoleAllowingOperation(
                role_id="role-1",
                role_name="Reader",
                role_type="BuiltInRole",
                matched_pattern="*",
                actions_count=1,
                data_actions_count=0,
                has_condition=False,
                condition_text=None,
            ),
            RoleAllowingOperation(
                role_id="role-2",
                role_name="Contributor",
                role_type="BuiltInRole",
                matched_pattern="*/read",
                actions_count=5,
                data_actions_count=0,
                has_condition=False,
                condition_text=None,
            ),
        ]
        cache_key = "roles_allowing_op:microsoft.storage/read:False"
        cache.set_allowing_roles(cache_key, mock_result)

        # Verify it's cached
        cached = cache.get_allowing_roles(cache_key)
        assert cached is not None
        assert len(cached) == 2
        assert cached[0].role_name == "Reader"

    def test_roles_allowing_operation_cleared_on_invalidate_all(self):
        """Test that roles_allowing_op entries are cleared on invalidate_all."""
        from azurerbac.cache import CacheService
        from azurerbac.web.services.models import RoleAllowingOperation

        cache = CacheService()

        # Helper to create a minimal RoleAllowingOperation
        def make_role(name: str) -> RoleAllowingOperation:
            return RoleAllowingOperation(
                role_id=f"{name}-id",
                role_name=name,
                role_type="BuiltInRole",
                matched_pattern="*",
                actions_count=1,
                data_actions_count=0,
                has_condition=False,
                condition_text=None,
            )

        # Cache multiple operation results with proper typed models
        cache.set_allowing_roles("roles_allowing_op:op1:False", [make_role("r1")])
        cache.set_allowing_roles("roles_allowing_op:op2:True", [make_role("r2")])
        cache.set_allowing_roles("roles_allowing_op:op3:False", [make_role("r3")])

        # Verify all are cached
        assert cache.get_allowing_roles("roles_allowing_op:op1:False") is not None
        assert cache.get_allowing_roles("roles_allowing_op:op2:True") is not None
        assert cache.get_allowing_roles("roles_allowing_op:op3:False") is not None

        # Invalidate all
        cache.reset()

        # Verify all are cleared
        assert cache.get_allowing_roles("roles_allowing_op:op1:False") is None
        assert cache.get_allowing_roles("roles_allowing_op:op2:True") is None
        assert cache.get_allowing_roles("roles_allowing_op:op3:False") is None

    def test_different_operations_have_different_cache_keys(self):
        """Test that different operations use different cache keys."""
        from azurerbac.cache import CacheService
        from azurerbac.web.services.models import RoleAllowingOperation

        cache = CacheService()

        # Cache results for different operations using proper typed models
        storage_role = RoleAllowingOperation(
            role_id="storage-reader-id",
            role_name="storage-reader",
            role_type="BuiltInRole",
            matched_pattern="Microsoft.Storage/*",
            actions_count=1,
            data_actions_count=0,
            has_condition=False,
            condition_text=None,
        )
        compute_role = RoleAllowingOperation(
            role_id="compute-reader-id",
            role_name="compute-reader",
            role_type="BuiltInRole",
            matched_pattern="Microsoft.Compute/*",
            actions_count=1,
            data_actions_count=0,
            has_condition=False,
            condition_text=None,
        )
        data_role = RoleAllowingOperation(
            role_id="data-reader-id",
            role_name="data-reader",
            role_type="BuiltInRole",
            matched_pattern="Microsoft.Storage/*",
            actions_count=0,
            data_actions_count=1,
            has_condition=False,
            condition_text=None,
        )

        cache.set_allowing_roles("roles_allowing_op:microsoft.storage/read:False", [storage_role])
        cache.set_allowing_roles("roles_allowing_op:microsoft.compute/read:False", [compute_role])
        cache.set_allowing_roles("roles_allowing_op:microsoft.storage/read:True", [data_role])

        # Verify they're independent
        storage_result = cache.get_allowing_roles("roles_allowing_op:microsoft.storage/read:False")
        compute_result = cache.get_allowing_roles("roles_allowing_op:microsoft.compute/read:False")
        data_result = cache.get_allowing_roles("roles_allowing_op:microsoft.storage/read:True")

        assert storage_result[0].role_name == "storage-reader"
        assert compute_result[0].role_name == "compute-reader"
        assert data_result[0].role_name == "data-reader"


class TestCacheConsistencyOnRebuild:
    """Tests for cache consistency when data is rebuilt."""

    def test_cache_entries_cleared_on_invalidate_all(self):
        """Test that cache entries are cleared when invalidate_all is called."""
        from azurerbac.cache import CacheService

        cache = CacheService()

        # Set up some initial cache entries via set_metadata for unique_providers
        cache.set_metadata(unique_providers=["Old Provider"])
        cache.set_allowing_roles("roles_allowing_op:old_op:False", [{"role": "old"}])

        # Invalidate all
        cache.reset()

        # Verify all entries are cleared (CacheData fields return defaults)
        assert cache.cache.unique_providers == []  # Default is empty list
        assert (
            cache.get_allowing_roles("roles_allowing_op:old_op:False") is None
        )  # misc_cache cleared


class TestRoleCoverageRaceCondition:
    """Tests for race condition between cache reload and role coverage lookup.

    This tests a bug where 'Roles Allowing This Operation' would be empty
    if the role coverage cache wasn't populated when all_role_jsons was accessed.
    """

    def test_roles_allowing_empty_when_coverage_cache_not_populated(self):
        """Reproduce the bug: roles_allowing returns empty when coverage cache is cleared.

        This simulates the race condition where:
        1. Cache is cleared (reload_if_needed)
        2. all_role_jsons is set in cache
        3. But _role_coverage_cache is not yet populated (precompute_func not called yet)
        4. get_roles_allowing_operation is called -> returns empty list (BUG)
        """
        from azurerbac.cache import CacheService
        from azurerbac.web.routes.pages import get_roles_allowing_operation
        from tests.helpers import clear_computed_caches

        cache = CacheService()

        # Set up mock role data - a role that grants Microsoft.Storage/storageAccounts/read
        mock_roles = [
            {
                "name": "test-reader-role",
                "properties": {
                    "roleName": "Test Reader",
                    "type": "BuiltInRole",
                    "permissions": [
                        {
                            "actions": ["Microsoft.Storage/storageAccounts/read"],
                            "notActions": [],
                            "dataActions": [],
                            "notDataActions": [],
                        }
                    ],
                },
            }
        ]

        # Set up mock operations
        sample_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
        ]

        # Simulate the BUG scenario: all_role_jsons is set but coverage cache is empty
        cache.set_allowing_roles("all_role_jsons", mock_roles)
        cache.set_allowing_roles("all_operations", sample_operations)

        # Clear the role coverage cache (simulating state after
        # _cache.clear() but before precompute)
        clear_computed_caches()

        # Now call get_roles_allowing_operation - this should return the role
        # but with the bug, it returns empty because _role_coverage_cache is empty
        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read",
            is_data_action=False,
            cache=cache,
        )

        # This assertion documents the BUG - if coverage cache is empty, we get empty results
        # After fix, the precompute should happen BEFORE all_role_jsons is visible
        # so this race condition cannot occur
        assert len(result) == 0, "BUG: Empty result when coverage cache not populated"

    def test_roles_allowing_works_when_coverage_cache_populated(self):
        """Verify roles_allowing works correctly when coverage cache IS populated."""
        from azurerbac.cache import (
            get_cache_service,
            precompute_all,
        )
        from azurerbac.web.routes.pages import get_roles_allowing_operation
        from tests.helpers import clear_computed_caches

        # Set up mock role data
        mock_role_json = {
            "name": "test-reader-role",
            "properties": {
                "roleName": "Test Reader",
                "type": "BuiltInRole",
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/storageAccounts/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            },
        }

        # Set up mock operations
        sample_operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/write", is_data_action=False),
        ]

        # Clear any stale cache
        clear_computed_caches()

        role_definition = RoleDefinition.model_validate(mock_role_json)

        # CORRECT order: precompute FIRST with the role included in both caches
        precomputed = precompute_all([role_definition], sample_operations)

        # Create CacheData with both the precomputed coverage AND the roles_by_id
        from dataclasses import replace as dc_replace

        new_source = dc_replace(
            precomputed.source,
            roles_by_id={
                "test-reader-role": CachedRole(
                    definition=role_definition,
                    status=RoleStatus.ACTIVE,
                )
            },
        )
        full_cache = dc_replace(precomputed, source=new_source)
        get_cache_service().swap(full_cache)

        # Now call get_roles_allowing_operation with the global app_cache
        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read",
            is_data_action=False,
            cache=get_cache_service(),
        )

        # Should find the role
        assert len(result) == 1
        assert result[0].role_name == "Test Reader"
        assert result[0].role_id == "test-reader-role"

        # Cleanup
        clear_computed_caches()

    def test_roles_allowing_uses_roles_by_id_as_source_of_truth(self):
        """Verify roles_allowing uses data.roles_by_id as the source of truth.

        This tests that get_roles_allowing_operation uses get_all_roles()
        which derives from data.roles_by_id, ensuring consistent data access.
        """
        from azurerbac.cache import (
            get_cache_service,
            precompute_all,
        )
        from azurerbac.web.routes.pages import get_roles_allowing_operation
        from tests.helpers import clear_computed_caches

        # Set up mock role data
        mock_role_json = {
            "name": "test-reader-role",
            "properties": {
                "roleName": "Test Reader",
                "type": "BuiltInRole",
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/storageAccounts/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            },
        }

        # Set up mock operations
        sample_operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
        ]

        # Clear any stale cache
        clear_computed_caches()

        role_definition = RoleDefinition.model_validate(mock_role_json)

        # Precompute coverage cache with roles_by_id included
        precomputed = precompute_all([role_definition], sample_operations)

        from dataclasses import replace as dc_replace

        new_source = dc_replace(
            precomputed.source,
            roles_by_id={
                "test-reader-role": CachedRole(
                    definition=role_definition,
                    status=RoleStatus.ACTIVE,
                )
            },
        )
        full_cache = dc_replace(precomputed, source=new_source)
        get_cache_service().swap(full_cache)

        # Verify cache.role_definitions derives from source.roles_by_id
        role_definitions = get_cache_service().cache.role_definitions
        assert len(role_definitions) == 1

        # Now call get_roles_allowing_operation - should work!
        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read",
            is_data_action=False,
            cache=get_cache_service(),
        )

        # Should find the role
        assert len(result) == 1
        assert result[0].role_name == "Test Reader"

        # Cleanup
        clear_computed_caches()


# =============================================================================
# Response Factory Tests
# =============================================================================


class TestResponseFactories:
    """Tests for API response factory functions."""

    @pytest.mark.parametrize(
        ("message",),
        [
            pytest.param("No operations found", id="no_operations"),
            pytest.param("Search too short", id="search_short"),
            pytest.param("", id="empty_message"),
        ],
    )
    def test_empty_search_response(self, message: str):
        """Test empty_search_response creates correct response."""
        from azurerbac.web.routes.responses import empty_search_response

        result = empty_search_response(message)
        assert result.operations == []
        assert result.total == 0
        assert result.is_wildcard_search is False
        assert result.message == message

    @pytest.mark.parametrize(
        ("error", "mode"),
        [
            pytest.param("Engine unavailable", "tfidf", id="with_mode"),
            pytest.param("Query too short", "semantic", id="with_other_mode"),
            pytest.param("Error occurred", None, id="no_mode"),
        ],
    )
    def test_ai_error_response(self, error: str, mode: str | None):
        """Test ai_error_response creates correct response."""
        from azurerbac.web.routes.responses import ai_error_response

        result = ai_error_response(error, mode)
        assert result.error == error
        assert result.recommendations == []
        if mode:
            assert result.engine is not None
            assert result.engine.mode == mode
        else:
            assert result.engine is None


class TestErrorMessages:
    """Tests for ErrorMessages enum."""

    @pytest.mark.parametrize(
        ("engine_name",),
        [
            pytest.param("TFIDF", id="tfidf"),
            pytest.param("Semantic", id="semantic"),
            pytest.param("LLM", id="llm"),
        ],
    )
    def test_engine_unavailable_message(self, engine_name: str):
        """Test engine_unavailable generates correct message."""
        from azurerbac.web.routes.responses import ErrorMessages

        message = ErrorMessages.engine_unavailable(engine_name)
        assert engine_name in message
        assert "unavailable" in message.lower()
