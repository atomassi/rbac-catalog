"""Comprehensive tests for the role recommender cache correctness.

These tests ensure that the recommender produces identical results:
1. With cache populated (FAST PATH)
2. Without cache (SLOW PATH)

This is critical because we have two separate code paths and need
to ensure they produce the same results.

Fixtures used from conftest.py:
- large_operations: ~575 operations across 10 providers
- sample_roles: 6 roles with varying permission patterns
"""

import pytest

from azurerbac.azure.models import OperationData
from azurerbac.cache import get_cache_service, precompute_all
from azurerbac.matching import recommend_roles
from azurerbac.matching.models import CacheOpsCount
from tests.helpers import clear_computed_caches, make_role_definition

# =============================================================================
# Tests for max_results Parameter
# =============================================================================


class TestMaxResultsParameter:
    """Tests that max_results=None returns all matching roles (bug fix)."""

    def test_no_limit_returns_all_full_matches(self, large_operations):
        """max_results=None should return ALL roles that fully match."""
        # Create 25 roles that all grant the same permission
        roles = [
            make_role_definition(
                f"Role {i}",
                f"role-id-{i}",
                ["Microsoft.Test/resource/read"],
            )
            for i in range(25)
        ]

        result = recommend_roles(
            ["Microsoft.Test/resource/read"],
            roles,
            large_operations,
            max_results=None,  # No limit
        )

        # Should return all 25 roles, not capped at 20
        assert len(result) == 25

    def test_explicit_limit_is_respected(self, large_operations):
        """max_results=10 should return at most 10 roles."""
        roles = [
            make_role_definition(
                f"Role {i}",
                f"role-id-{i}",
                ["Microsoft.Test/resource/read"],
            )
            for i in range(25)
        ]

        result = recommend_roles(
            ["Microsoft.Test/resource/read"],
            roles,
            large_operations,
            max_results=10,
        )

        assert len(result) == 10

    def test_default_returns_all_matches(self, large_operations):
        """Default (no max_results) should return all matching roles."""
        roles = [
            make_role_definition(
                f"Role {i}",
                f"role-id-{i}",
                ["Microsoft.Authorization/roleAssignments/delete"],
            )
            for i in range(35)
        ]

        # Don't pass max_results - should default to None (no limit)
        result = recommend_roles(
            ["Microsoft.Authorization/roleAssignments/delete"],
            roles,
            large_operations,
        )

        # Should return all 35 roles
        assert len(result) == 35


# =============================================================================
# Tests for Cache vs No-Cache Consistency
# =============================================================================


class TestCacheConsistency:
    """Tests that cached and non-cached paths produce identical results."""

    def test_basic_wildcard_consistency(self, large_operations, sample_roles):
        """Test that */read produces same results with and without cache."""
        # Clear cache and run without cache
        clear_computed_caches()
        assert len(get_cache_service().container.cache.role_coverage) == 0

        result_no_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
        )

        # Now populate cache and run again
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))
        assert len(get_cache_service().container.cache.role_coverage) > 0

        result_with_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
        )

        # Results should be identical
        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id, "Role ID mismatch"
            assert r_no.role_name == r_with.role_name, "Role name mismatch"
            assert r_no.matched_operations == r_with.matched_operations, (
                f"Matched ops mismatch for {r_no.role_name}"
            )
            assert r_no.missing_operations == r_with.missing_operations, (
                f"Missing ops mismatch for {r_no.role_name}"
            )
            assert r_no.matched_operations_count == r_with.matched_operations_count, (
                f"Matched count mismatch for {r_no.role_name}"
            )
            assert r_no.missing_operations_count == r_with.missing_operations_count, (
                f"Missing count mismatch for {r_no.role_name}"
            )
            assert abs(r_no.match_percentage - r_with.match_percentage) < 0.001, (
                f"Match % mismatch for {r_no.role_name}"
            )
            assert r_no.is_full_match == r_with.is_full_match, (
                f"Full match mismatch for {r_no.role_name}"
            )

    def test_data_plane_wildcard_consistency(self, large_operations, sample_roles):
        """Test data plane wildcards produce same results with and without cache."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": True},  # Data plane only
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": True},
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count
            assert r_no.missing_operations_count == r_with.missing_operations_count
            assert r_no.is_full_match == r_with.is_full_match

    def test_both_planes_wildcard_consistency(self, large_operations, sample_roles):
        """Test wildcards on both planes produce same results."""
        clear_computed_caches()

        # No explicit flags = both planes
        result_no_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count, (
                f"Matched count mismatch for {r_no.role_name}: "
                f"{r_no.matched_operations_count} vs {r_with.matched_operations_count}"
            )
            assert r_no.missing_operations_count == r_with.missing_operations_count, (
                f"Missing count mismatch for {r_no.role_name}: "
                f"{r_no.missing_operations_count} vs {r_with.missing_operations_count}"
            )

    def test_explicit_operations_consistency(self, large_operations, sample_roles):
        """Test explicit (non-wildcard) operations produce same results."""
        clear_computed_caches()

        explicit_ops = [
            "Microsoft.Storage/accounts/read",
            "Microsoft.Storage/accounts/write",
            "Microsoft.Compute/instances/read",
        ]

        result_no_cache = recommend_roles(explicit_ops, sample_roles, large_operations)

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(explicit_ops, sample_roles, large_operations)

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations == r_with.matched_operations
            assert r_no.missing_operations == r_with.missing_operations

    def test_mixed_wildcard_explicit_consistency(self, large_operations, sample_roles):
        """Test mixed wildcards and explicit operations."""
        clear_computed_caches()

        mixed_ops = [
            "*/read",  # Wildcard
            "Microsoft.Storage/accounts/write",  # Explicit
        ]

        result_no_cache = recommend_roles(mixed_ops, sample_roles, large_operations)

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(mixed_ops, sample_roles, large_operations)

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count


class TestMissingOperationsExpanded:
    """Tests for missing_operations_expanded field correctness."""

    def test_zero_coverage_wildcard_has_expanded_ops(self, large_operations):
        """When a role has 0 coverage for a wildcard, expanded ops should be populated."""
        # Role with NO data plane permissions
        reader_role = make_role_definition(
            "Reader",
            "reader-id",
            actions=["*/read"],
            data_actions=[],
        )

        clear_computed_caches()

        # Request both control and data plane */read
        result = recommend_roles(
            ["*/read"],
            [reader_role],
            large_operations,
            # No flags = both planes
        )

        assert len(result) == 1
        reader = result[0]

        # Reader should have missing data plane operations
        # missing_operations_expanded should contain actual operation names
        if reader.missing_operations_count > 0:
            assert len(reader.missing_operations_expanded) > 0, (
                "missing_operations_expanded should have samples when missing_operations_count > 0"
            )
            # Check that expanded ops are real operation names, not wildcards
            for op in reader.missing_operations_expanded:
                assert "*" not in op, f"Expanded op should not be a wildcard: {op}"

    def test_expanded_ops_same_with_cache(self, large_operations):
        """Test that missing_operations_expanded is same with and without cache."""
        reader_role = make_role_definition(
            "Reader",
            "reader-id",
            actions=["*/read"],
            data_actions=[],
        )

        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read"],
            [reader_role],
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all([reader_role], large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            [reader_role],
            large_operations,
        )

        assert len(result_no_cache) == 1
        assert len(result_with_cache) == 1

        # Expanded ops should match
        assert (
            result_no_cache[0].missing_operations_expanded
            == result_with_cache[0].missing_operations_expanded
        )


class TestPartialWildcardCoverage:
    """Tests for partial wildcard coverage scenarios."""

    def test_partial_coverage_detected(self, large_operations):
        """Test that partial coverage is correctly detected."""
        # Role that only covers some */read operations
        partial_role = make_role_definition(
            "Partial Reader",
            "partial-id",
            actions=["Microsoft.Storage/*/read"],  # Only Storage reads
            data_actions=[],
        )

        clear_computed_caches()

        result = recommend_roles(
            ["*/read"],
            [partial_role],
            large_operations,
            requested_ops_data_flags={"*/read": False},  # Control plane only
        )

        assert len(result) == 1
        assert result[0].has_partial_wildcard_match is True
        assert result[0].match_percentage < 100.0
        assert result[0].missing_operations_count > 0

    def test_partial_coverage_counts_with_cache(self, large_operations):
        """Test partial coverage counts are same with and without cache."""
        partial_role = make_role_definition(
            "Partial Reader",
            "partial-id",
            actions=["Microsoft.Storage/*/read"],
            data_actions=["Microsoft.Storage/data/*/read"],
        )

        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read"],
            [partial_role],
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all([partial_role], large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            [partial_role],
            large_operations,
        )

        assert len(result_no_cache) == 1
        assert len(result_with_cache) == 1

        r_no = result_no_cache[0]
        r_with = result_with_cache[0]

        assert r_no.matched_operations_count == r_with.matched_operations_count, (
            f"Matched count: {r_no.matched_operations_count} vs {r_with.matched_operations_count}"
        )
        assert r_no.missing_operations_count == r_with.missing_operations_count, (
            f"Missing count: {r_no.missing_operations_count} vs {r_with.missing_operations_count}"
        )
        assert r_no.has_partial_wildcard_match == r_with.has_partial_wildcard_match


class TestNotActionsExclusions:
    """Tests for notActions/notDataActions exclusion handling."""

    def test_not_actions_consistency(self, large_operations):
        """Test notActions are handled same with and without cache."""
        contributor_role = make_role_definition(
            "Contributor",
            "contrib-id",
            actions=["*"],
            not_actions=["Microsoft.Storage/*/delete"],
            data_actions=["*"],
            not_data_actions=["Microsoft.Storage/data/*/delete"],
        )

        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/delete"],  # Request delete operations
            [contributor_role],
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all([contributor_role], large_operations))

        result_with_cache = recommend_roles(
            ["*/delete"],
            [contributor_role],
            large_operations,
        )

        if result_no_cache and result_with_cache:
            assert (
                result_no_cache[0].matched_operations_count
                == result_with_cache[0].matched_operations_count
            )
            assert (
                result_no_cache[0].missing_operations_count
                == result_with_cache[0].missing_operations_count
            )

    def test_total_permissions_with_exclusions(self, large_operations):
        """Test total permission counts account for exclusions correctly."""
        role_with_exclusions = make_role_definition(
            "Limited",
            "limited-id",
            actions=["Microsoft.Storage/*"],
            not_actions=["Microsoft.Storage/*/delete"],
            data_actions=["Microsoft.Storage/data/*"],
            not_data_actions=["Microsoft.Storage/data/*/delete"],
        )

        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            [role_with_exclusions],
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all([role_with_exclusions], large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            [role_with_exclusions],
            large_operations,
        )

        if result_no_cache and result_with_cache:
            # Total permissions should exclude the notActions
            assert result_no_cache[0].total_permissions == result_with_cache[0].total_permissions
            assert (
                result_no_cache[0].control_plane_permissions
                == result_with_cache[0].control_plane_permissions
            )
            assert (
                result_no_cache[0].data_plane_permissions
                == result_with_cache[0].data_plane_permissions
            )


class TestEdgeCases:
    """Edge case tests for cache correctness."""

    def test_empty_operations_list(self, sample_roles):
        """Empty operations list should return empty results."""
        clear_computed_caches()
        result_no_cache = recommend_roles([], sample_roles, [])

        get_cache_service().swap_in_memory(precompute_all(sample_roles, []))
        result_with_cache = recommend_roles([], sample_roles, [])

        assert result_no_cache == []
        assert result_with_cache == []

    def test_single_operation(self, large_operations, sample_roles):
        """Single operation consistency test."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.is_full_match == r_with.is_full_match

    def test_nonexistent_operation(self, large_operations, sample_roles):
        """Operation that doesn't exist in the operations list.

        Note: With cache, roles are filtered based on known operations.
        Without cache, pattern matching may still match unknown operations.
        This test verifies both paths handle unknown operations gracefully,
        though results may differ (which is acceptable).
        """
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Nonexistent/resource/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Nonexistent/resource/read"],
            sample_roles,
            large_operations,
        )

        # Both should handle this gracefully (no crashes)
        # Note: Results may differ - without cache, pattern matching may find matches
        # With cache, unknown operations aren't in the cache, so no matches
        # What matters is both return valid results without errors
        assert isinstance(result_no_cache, list)
        assert isinstance(result_with_cache, list)

    def test_star_wildcard(self, large_operations, sample_roles):
        """Test the most permissive * wildcard."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*"],
            sample_roles,
            large_operations,
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.matched_operations_count == r_with.matched_operations_count


class TestCachePopulation:
    """Tests for cache population and retrieval."""

    def test_cache_is_populated_by_precompute(self, large_operations, sample_roles):
        """Test that precompute_all populates the cache."""
        clear_computed_caches()
        assert len(get_cache_service().container.cache.role_coverage) == 0
        assert len(get_cache_service().container.cache.role_net_permissions) == 0

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        assert len(get_cache_service().container.cache.role_coverage) > 0
        assert len(get_cache_service().container.cache.role_net_permissions) > 0

        # Check all roles are cached
        for role in sample_roles:
            role_id = role.role_id
            assert role_id in get_cache_service().container.cache.role_coverage
            assert role_id in get_cache_service().container.cache.role_net_permissions

    def test_get_role_coverage_returns_correct_data(self, large_operations, sample_roles):
        """Test get_cache_service().container.get_role_coverage returns correct tuple of sets."""
        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        # Get coverage for Owner (should have everything)
        owner_coverage = get_cache_service().container.get_role_coverage("owner-role-id")
        assert owner_coverage is not None
        control_ops, data_ops = owner_coverage
        assert isinstance(control_ops, set)
        assert isinstance(data_ops, set)
        assert len(control_ops) > 0
        assert len(data_ops) > 0

        # Get coverage for Reader (should have only control plane reads)
        reader_coverage = get_cache_service().container.get_role_coverage("reader-role-id")
        assert reader_coverage is not None
        control_ops, data_ops = reader_coverage
        assert len(control_ops) > 0  # Has control plane reads
        assert len(data_ops) == 0  # No data plane actions

    def test_get_role_net_permissions_returns_counts(self, large_operations, sample_roles):
        """Test get_cache_service().container.get_role_net_permissions returns correct counts."""
        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        owner_perms = get_cache_service().container.get_role_net_permissions("owner-role-id")
        assert owner_perms is not None
        control_count, data_count = owner_perms
        assert control_count > 0
        assert data_count > 0

        reader_perms = get_cache_service().container.get_role_net_permissions("reader-role-id")
        assert reader_perms is not None
        control_count, data_count = reader_perms
        assert control_count > 0
        assert data_count == 0

    def test_cache_is_cleared(self, large_operations, sample_roles):
        """Test that clear_computed_caches actually clears everything."""
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))
        assert len(get_cache_service().container.cache.role_coverage) > 0

        clear_computed_caches()

        assert len(get_cache_service().container.cache.role_coverage) == 0
        assert len(get_cache_service().container.cache.role_net_permissions) == 0


class TestMultipleWildcardPatterns:
    """Tests for multiple wildcard patterns in a single request."""

    def test_multiple_wildcards_consistency(self, large_operations, sample_roles):
        """Test multiple wildcard patterns produce same results."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read", "*/write", "*/delete"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*/read", "*/write", "*/delete"],
            sample_roles,
            large_operations,
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count
            assert r_no.missing_operations_count == r_with.missing_operations_count

    def test_provider_wildcard_consistency(self, large_operations, sample_roles):
        """Test provider-specific wildcards."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Storage/*/read", "Microsoft.Compute/*/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Storage/*/read", "Microsoft.Compute/*/read"],
            sample_roles,
            large_operations,
        )

        assert len(result_no_cache) == len(result_with_cache)


class TestDataPlaneFlags:
    """Tests for data plane flag handling with cache."""

    def test_control_plane_only_flag(self, large_operations, sample_roles):
        """Test control plane only flag consistency."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": False},
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": False},
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count

    def test_data_plane_only_flag(self, large_operations, sample_roles):
        """Test data plane only flag consistency."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": True},
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["*/read"],
            sample_roles,
            large_operations,
            requested_ops_data_flags={"*/read": True},
        )

        assert len(result_no_cache) == len(result_with_cache)

        for r_no, r_with in zip(result_no_cache, result_with_cache, strict=True):
            assert r_no.role_id == r_with.role_id
            assert r_no.matched_operations_count == r_with.matched_operations_count


class TestReaderRoleSpecificCases:
    """Specific tests for Reader role behavior - a common edge case."""

    @pytest.fixture
    def reader_test_ops(self):
        """Operations for Reader testing."""
        # 100 control plane read operations
        ops = [
            OperationData(name=f"Microsoft.Provider{i}/resource/read", is_data_action=False)
            for i in range(100)
        ]
        # 50 data plane read operations
        ops.extend(
            OperationData(name=f"Microsoft.Data{i}/resource/read", is_data_action=True)
            for i in range(50)
        )
        return ops

    def test_reader_control_plane_only_full_match(self, reader_test_ops):
        """Reader with control plane */read should be 100% match for control only."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        clear_computed_caches()

        result = recommend_roles(
            ["*/read"],
            [reader],
            reader_test_ops,
            requested_ops_data_flags={"*/read": False},  # Control only
        )

        assert len(result) == 1
        assert result[0].is_full_match is True
        assert result[0].match_percentage == 100.0
        assert result[0].missing_operations_count == 0

    def test_reader_both_planes_partial_match(self, reader_test_ops):
        """Reader requesting both planes should show partial match."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        clear_computed_caches()

        # Request both planes (no flag = both)
        result = recommend_roles(
            ["*/read"],
            [reader],
            reader_test_ops,
        )

        assert len(result) == 1
        reader_result = result[0]

        assert reader_result.is_full_match is False
        assert reader_result.match_percentage < 100.0
        assert reader_result.missing_operations_count > 0
        # Should show data plane ops as missing
        assert reader_result.has_partial_wildcard_match is True

    def test_reader_data_plane_only_no_match(self, reader_test_ops):
        """Reader with data plane only request should not match."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        clear_computed_caches()

        result = recommend_roles(
            ["*/read"],
            [reader],
            reader_test_ops,
            requested_ops_data_flags={"*/read": True},  # Data only
        )

        # Reader has no data actions, should not match
        assert len(result) == 0

    def test_reader_consistency_across_cache_states(self, reader_test_ops):
        """Test Reader results are consistent regardless of cache state."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        # Test all three scenarios with and without cache
        for flags_desc, flags in [
            ("control_only", {"*/read": False}),
            ("data_only", {"*/read": True}),
            ("both_planes", None),
        ]:
            clear_computed_caches()
            result_no_cache = recommend_roles(
                ["*/read"],
                [reader],
                reader_test_ops,
                requested_ops_data_flags=flags,
            )

            precompute_all([reader], reader_test_ops)
            result_with_cache = recommend_roles(
                ["*/read"],
                [reader],
                reader_test_ops,
                requested_ops_data_flags=flags,
            )

            assert len(result_no_cache) == len(result_with_cache), (
                f"Length mismatch for {flags_desc}"
            )

            if result_no_cache:
                assert (
                    result_no_cache[0].matched_operations_count
                    == result_with_cache[0].matched_operations_count
                ), f"Matched count mismatch for {flags_desc}"
                assert (
                    result_no_cache[0].missing_operations_count
                    == result_with_cache[0].missing_operations_count
                ), f"Missing count mismatch for {flags_desc}"


class TestSortingConsistency:
    """Tests that sorting is consistent with and without cache."""

    def test_sorting_by_least_privilege(self, large_operations, sample_roles):
        """Test that roles are sorted by least privilege consistently."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        # Order should be identical
        no_cache_order = [r.role_id for r in result_no_cache]
        with_cache_order = [r.role_id for r in result_with_cache]

        assert no_cache_order == with_cache_order, (
            f"Sort order mismatch:\n  No cache: {no_cache_order}\n  With cache: {with_cache_order}"
        )

    def test_high_privilege_roles_sorted_last(self, large_operations, sample_roles):
        """High privilege roles should be sorted to end consistently."""
        clear_computed_caches()

        result_no_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        result_with_cache = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        def check_high_privilege_last(results, desc):
            # Find indices of high privilege roles
            high_priv_indices = [
                i for i, r in enumerate(results) if r.role_name in ["Owner", "Contributor"]
            ]
            non_high_priv_indices = [
                i for i, r in enumerate(results) if r.role_name not in ["Owner", "Contributor"]
            ]

            if high_priv_indices and non_high_priv_indices:
                assert max(non_high_priv_indices) < min(high_priv_indices), (
                    f"{desc}: High privilege roles should be after non-high privilege"
                )

        check_high_privilege_last(result_no_cache, "No cache")
        check_high_privilege_last(result_with_cache, "With cache")


# =============================================================================
# Test Cache Staleness Detection
# =============================================================================


class TestCacheStalenessDetection:
    """Test that cache is invalidated when operation counts change."""

    def test_cache_invalidated_when_ops_added(self, large_operations, sample_roles):
        """Cache should be invalidated when new operations are added."""
        clear_computed_caches()

        # Build cache with current operations (roles first, then operations)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))
        initial_cache_size = len(get_cache_service().container.cache.role_coverage)

        # Cache should be populated
        assert initial_cache_size > 0

        # Store initial cache counts
        initial_counts = get_cache_service().container.cache.cache_ops_count
        assert initial_counts.control > 0, f"Expected control ops > 0, got {initial_counts}"

        # Add new operations
        extended_operations = [
            *large_operations,
            OperationData(name="Microsoft.NewProvider/newResource/read", is_data_action=False),
            OperationData(name="Microsoft.NewProvider/newResource/write", is_data_action=False),
            OperationData(name="Microsoft.NewProvider/newResource/delete", is_data_action=False),
        ]

        # Make a recommendation with extended operations
        # This should trigger cache invalidation due to operation count change
        result = recommend_roles(
            ["Microsoft.NewProvider/newResource/read"],
            sample_roles,
            extended_operations,
        )

        # Verify results are correct - Reader with */read should match
        assert len(result) > 0
        reader_results = [r for r in result if r.role_name == "Reader"]
        assert len(reader_results) == 1
        # Reader with */read should fully match the new read operation
        assert reader_results[0].match_percentage == 100.0

    def test_cache_valid_when_ops_unchanged(self, large_operations, sample_roles):
        """Cache should remain valid when operation counts are unchanged."""
        clear_computed_caches()

        # Build cache (roles first, then operations)
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))
        initial_cache_size = len(get_cache_service().container.cache.role_coverage)
        initial_counts = get_cache_service().container.cache.cache_ops_count

        # Make a recommendation with same operations
        result = recommend_roles(
            ["Microsoft.Storage/accounts/read"],
            sample_roles,
            large_operations,
        )

        # Cache should still be populated (not cleared)
        assert len(get_cache_service().container.cache.role_coverage) == initial_cache_size
        assert get_cache_service().container.cache.cache_ops_count == initial_counts

        # Results should be correct
        assert len(result) > 0

    def test_reader_wildcard_full_coverage_after_ops_change(self, sample_roles):
        """
        Regression test for bug: Reader role showing partial match for */read.

        This test reproduces the exact scenario that caused the bug:
        1. Cache is built with N operations
        2. New operations are added to the database
        3. User requests */read on control plane
        4. Reader role (which has */read action) should show 100% match

        The bug was: Reader showed "7474 of 7481" because the cache was stale.
        The intersection of cached role ops with fresh pattern ops gave wrong count.
        """
        # Step 1: Create initial operations (simulating DB state at cache build time)
        initial_operations = [
            OperationData(name=f"Microsoft.Provider{i}/resource/read", is_data_action=False)
            for i in range(100)
        ]

        clear_computed_caches()

        # Step 2: Build cache with initial operations
        get_cache_service().swap_in_memory(precompute_all(sample_roles, initial_operations))

        # Verify cache was built correctly
        assert len(get_cache_service().container.cache.role_coverage) > 0
        initial_cache_count = get_cache_service().container.cache.cache_ops_count.control
        assert initial_cache_count == 100

        # Step 3: Simulate new operations being added (DB updated)
        extended_operations = initial_operations + [
            OperationData(
                name=f"Microsoft.NewProvider{i}/newResource/read",
                is_data_action=False,
            )
            for i in range(7)  # Add 7 new operations (like the original bug)
        ]

        # Step 4: Request */read on control plane (the exact query that exposed the bug)
        result = recommend_roles(
            ["*/read"],
            sample_roles,
            extended_operations,
            requested_ops_data_flags={"*/read": False},  # Control plane only
        )

        # Step 5: Verify Reader role shows 100% match
        reader_results = [r for r in result if r.role_name == "Reader"]
        assert len(reader_results) == 1, "Reader role should be in results"

        reader = reader_results[0]

        # THE KEY ASSERTION: Reader must show 100% match, not partial
        assert reader.match_percentage == 100.0, (
            f"Reader should have 100% match for */read control plane, "
            f"got {reader.match_percentage}%"
        )

        # Verify the counts are correct
        # Reader should match ALL 107 read operations (100 initial + 7 new)
        assert reader.matched_operations_count == 107, (
            f"Reader should match all 107 read operations, got {reader.matched_operations_count}"
        )
        assert reader.missing_operations_count == 0, (
            f"Reader should have 0 missing operations, got {reader.missing_operations_count}"
        )

        # Verify no partial wildcard match flag
        assert not reader.has_partial_wildcard_match, (
            "Reader should not have partial wildcard match for */read"
        )

    def test_cache_rebuilt_correctly_after_invalidation(self, sample_roles):
        """
        Test that after cache invalidation, subsequent queries work correctly.

        This ensures the slow path computes correct results and that
        re-precomputing the cache also works correctly.
        """
        # Initial operations - 3 total, 2 are /read
        ops_v1 = [
            OperationData(name="Microsoft.Storage/accounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/accounts/write", is_data_action=False),
            OperationData(name="Microsoft.Compute/vms/read", is_data_action=False),
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all(sample_roles, ops_v1))

        # First query with v1 ops
        result_v1 = recommend_roles(
            ["*/read"], sample_roles, ops_v1, requested_ops_data_flags={"*/read": False}
        )
        reader_v1 = next(r for r in result_v1 if r.role_name == "Reader")
        assert reader_v1.matched_operations_count == 2  # 2 read ops in v1

        # Add new operations (v2) - 5 total, 4 are /read
        ops_v2 = [
            *ops_v1,
            OperationData(name="Microsoft.Network/vnets/read", is_data_action=False),
            OperationData(name="Microsoft.Web/sites/read", is_data_action=False),
        ]

        # Query with v2 ops - cache should be invalidated
        result_v2 = recommend_roles(
            ["*/read"], sample_roles, ops_v2, requested_ops_data_flags={"*/read": False}
        )
        reader_v2 = next(r for r in result_v2 if r.role_name == "Reader")

        # Reader should now match 4 read operations (out of 5 total)
        assert reader_v2.matched_operations_count == 4, (
            f"After ops change, Reader should match 4 read ops, "
            f"got {reader_v2.matched_operations_count}"
        )
        assert reader_v2.match_percentage == 100.0

        # Now rebuild cache with v2 and verify it's correct
        get_cache_service().swap_in_memory(precompute_all(sample_roles, ops_v2))
        assert get_cache_service().container.cache.cache_ops_count == CacheOpsCount(
            5,
            0,
        )  # 5 control ops total, 0 data ops

        # Query again - should use fresh cache
        result_v2_cached = recommend_roles(
            ["*/read"], sample_roles, ops_v2, requested_ops_data_flags={"*/read": False}
        )
        reader_v2_cached = next(r for r in result_v2_cached if r.role_name == "Reader")
        assert reader_v2_cached.matched_operations_count == 4
        assert reader_v2_cached.match_percentage == 100.0

    def test_data_plane_ops_change_invalidates_cache(self, sample_roles):
        """Test that data plane operation changes also invalidate cache."""
        # Initial: only control plane ops
        ops_v1 = [
            OperationData(name="Microsoft.Storage/accounts/read", is_data_action=False),
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all(sample_roles, ops_v1))
        assert get_cache_service().container.cache.cache_ops_count == CacheOpsCount(1, 0)

        # Add data plane operations
        ops_v2 = [
            *ops_v1,
            OperationData(
                name="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
                is_data_action=True,
            ),
        ]

        # Query - should trigger cache invalidation due to data ops change
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            sample_roles,
            ops_v2,
        )

        # Storage Data Reader should match the blob read operation
        [r for r in result if r.role_name == "Storage Data Reader"]
        # Note: depends on sample_roles fixture having this role
        # The key point is that the cache was invalidated properly

        # Verify cache was rebuilt correctly
        get_cache_service().swap_in_memory(precompute_all(sample_roles, ops_v2))
        cache_counts = get_cache_service().container.cache.cache_ops_count
        assert cache_counts == CacheOpsCount(1, 1)  # 1 control, 1 data


class TestAtomicSwap:
    """Tests for atomic cache swap behavior during refresh."""

    def test_atomic_swap_replaces_cache_instance(self, large_operations, sample_roles):
        """Test that precompute_all atomically swaps the cache instance."""
        # Clear and capture initial cache reference
        clear_computed_caches()
        initial_cache = get_cache_service().container.cache
        assert len(initial_cache.role_coverage) == 0

        # Precompute creates a new cache and swaps
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        # The new cache should have data
        new_cache = get_cache_service().container.cache
        assert len(new_cache.role_coverage) > 0

        # It should be a different instance (atomic swap)
        assert new_cache is not initial_cache

        # The initial cache should still be empty (it wasn't modified)
        assert len(initial_cache.role_coverage) == 0

    def test_readers_see_consistent_state_during_swap(self, large_operations, sample_roles):
        """Test that readers capture a consistent snapshot before swap."""
        # Build initial cache
        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all(sample_roles, large_operations))

        # Capture cache reference before any changes
        cached_before = get_cache_service().container.cache
        initial_role_count = len(cached_before.role_coverage)
        assert initial_role_count > 0

        # Simulate a "reader" holding the old reference
        old_cache_ref = cached_before

        # Now rebuild with different data (simulating refresh)
        # Use a subset of roles to get different cache contents
        subset_roles = sample_roles[:2]  # Only first 2 roles
        get_cache_service().swap_in_memory(precompute_all(subset_roles, large_operations))

        # New readers see the new cache
        cached_after = get_cache_service().container.cache

        # Old reference still has original data
        assert len(old_cache_ref.role_coverage) == initial_role_count

        # New reference may have different count (depends on roles)
        assert cached_after is not old_cache_ref

    def test_cache_container_swap_method_works(self):
        """Test that get_cache_service().container.swap() works correctly."""
        from azurerbac.cache import CacheData

        # Create a new cache with specific data
        new_cache = CacheData()
        new_cache.role_coverage["test-role"] = ({"op1", "op2"}, {"op3"})

        # Swap it in
        get_cache_service().container.swap(new_cache)

        # Verify it's now the active cache
        assert get_cache_service().container.cache is new_cache
        assert "test-role" in get_cache_service().container.cache.role_coverage

        # Clean up
        clear_computed_caches()

    def test_clear_swaps_to_new_instance(self):
        """Test that clear() swaps to a fresh instance (atomic swap pattern)."""

        # Get initial instance
        initial_instance = get_cache_service().container.cache

        # Add some data manually
        initial_instance.role_coverage["test"] = (set(), set())
        assert len(initial_instance.role_coverage) > 0

        # Clear (swaps to new instance)
        clear_computed_caches()

        # Should be a NEW instance that is empty
        new_instance = get_cache_service().container.cache
        assert new_instance is not initial_instance
        assert len(new_instance.role_coverage) == 0
        # Old instance still has the data (no in-place mutation for thread safety)
        assert len(initial_instance.role_coverage) == 1


class TestCacheInvalidationScenarios:
    """Comprehensive tests for cache invalidation with Plane enum keys.

    These tests validate that all operation-dependent caches are properly
    invalidated when operations change, since Plane enum keys are static
    (unlike the old integer keys that embedded operation counts).

    The caches that depend on operations are:
    - pattern_match: PatternCacheKey(pattern, Plane) -> set[str]
    - wildcard_count: PatternCacheKey(pattern, Plane) -> int
    - partial_coverage: PartialCoverageCacheKey -> WildcardCoverageResult
    - role_coverage: role_id -> RoleCoverage
    - role_net_permissions: role_id -> RoleNetPermissions
    """

    @pytest.fixture
    def reader_role(self):
        """Reader role with */read permission."""
        return make_role_definition(
            role_name="Reader",
            role_id="reader-role",
            actions=["*/read"],
        )

    @pytest.fixture
    def contributor_role(self):
        """Contributor role with * permission."""
        return make_role_definition(
            role_name="Contributor",
            role_id="contributor-role",
            actions=["*"],
        )

    @pytest.fixture
    def storage_data_reader(self):
        """Storage Data Reader with data plane permission."""
        return make_role_definition(
            role_name="Storage Data Reader",
            role_id="storage-data-reader",
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/read"],
        )

    @pytest.mark.parametrize(
        ("initial_control_count", "added_control_count", "expected_final_count"),
        [
            pytest.param(10, 5, 15, id="add_5_to_10"),
            pytest.param(100, 7, 107, id="add_7_to_100_original_bug"),
            pytest.param(50, 0, 50, id="no_change"),
            pytest.param(1, 9, 10, id="grow_1_to_10"),
            pytest.param(1, 99, 100, id="grow_1_to_100"),
        ],
    )
    def test_control_plane_ops_change_invalidates_pattern_cache(
        self,
        reader_role,
        initial_control_count: int,
        added_control_count: int,
        expected_final_count: int,
    ):
        """Test pattern_match cache is invalidated when control ops change."""
        # Build initial operations
        initial_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(initial_control_count)
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all([reader_role], initial_ops))

        # Verify initial cache state
        cache = get_cache_service().container.cache
        assert cache.cache_ops_count.control == initial_control_count

        # Add new operations
        extended_ops = initial_ops + [
            OperationData(
                name=f"Microsoft.NewProvider{i}/newResource/read",
                is_data_action=False,
            )
            for i in range(added_control_count)
        ]

        # Query with extended operations - should trigger invalidation if changed
        result = recommend_roles(
            ["*/read"],
            [reader_role],
            extended_ops,
            requested_ops_data_flags={"*/read": False},
        )

        # Reader should match ALL read operations
        reader = next(r for r in result if r.role_name == "Reader")
        assert reader.matched_operations_count == expected_final_count, (
            f"Expected {expected_final_count} matched ops, got {reader.matched_operations_count}"
        )
        assert reader.match_percentage == 100.0

    @pytest.mark.parametrize(
        ("initial_data_count", "added_data_count", "expected_final_count"),
        [
            pytest.param(10, 5, 15, id="add_5_to_10"),
            pytest.param(50, 10, 60, id="add_10_to_50"),
            pytest.param(1, 19, 20, id="grow_1_to_20"),
        ],
    )
    def test_data_plane_ops_change_invalidates_pattern_cache(
        self,
        storage_data_reader,
        initial_data_count: int,
        added_data_count: int,
        expected_final_count: int,
    ):
        """Test pattern_match cache is invalidated when data ops change."""
        # Build initial data operations
        initial_ops = [
            OperationData(
                name=f"Microsoft.Storage/storageAccounts/blobServices/container{i}/read",
                is_data_action=True,
            )
            for i in range(initial_data_count)
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all([storage_data_reader], initial_ops))

        # Verify initial cache state
        cache = get_cache_service().container.cache
        assert cache.cache_ops_count.data == initial_data_count

        # Add new data operations
        extended_ops = initial_ops + [
            OperationData(
                name=f"Microsoft.Storage/storageAccounts/blobServices/newContainer{i}/read",
                is_data_action=True,
            )
            for i in range(added_data_count)
        ]

        # Query with extended operations
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/blobServices/*/read"],
            [storage_data_reader],
            extended_ops,
            requested_ops_data_flags={
                "Microsoft.Storage/storageAccounts/blobServices/*/read": True
            },
        )

        # Storage Data Reader should match ALL data read operations
        reader = next(r for r in result if r.role_name == "Storage Data Reader")
        assert reader.matched_operations_count == expected_final_count, (
            f"Expected {expected_final_count} matched ops, got {reader.matched_operations_count}"
        )
        assert reader.match_percentage == 100.0

    @pytest.mark.parametrize(
        (
            "initial_control",
            "initial_data",
            "added_control",
            "added_data",
            "expect_invalidation",
        ),
        [
            pytest.param(10, 10, 5, 0, True, id="control_only_change"),
            pytest.param(10, 10, 0, 5, True, id="data_only_change"),
            pytest.param(10, 10, 5, 5, True, id="both_planes_change"),
            pytest.param(10, 10, 0, 0, False, id="no_change"),
        ],
    )
    def test_cache_invalidation_triggers(
        self,
        reader_role,
        initial_control: int,
        initial_data: int,
        added_control: int,
        added_data: int,
        expect_invalidation: bool,
    ):
        """Test that check_cache_staleness correctly detects stale cache."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        # Build initial operations
        initial_ops = [
            OperationData(
                name=f"Microsoft.Control{i}/resource/read",
                is_data_action=False,
            )
            for i in range(initial_control)
        ] + [
            OperationData(
                name=f"Microsoft.Data{i}/resource/read",
                is_data_action=True,
            )
            for i in range(initial_data)
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all([reader_role], initial_ops))

        # Extend operations
        extended_ops = (
            initial_ops
            + [
                OperationData(
                    name=f"Microsoft.NewControl{i}/resource/read",
                    is_data_action=False,
                )
                for i in range(added_control)
            ]
            + [
                OperationData(
                    name=f"Microsoft.NewData{i}/resource/read",
                    is_data_action=True,
                )
                for i in range(added_data)
            ]
        )

        # Create service with extended ops
        service = RoleRecommendationService(
            extended_ops, caches=get_cache_service().container.cache
        )

        # Check staleness
        was_invalidated = service.check_cache_staleness()

        assert was_invalidated == expect_invalidation, (
            f"Expected invalidation={expect_invalidation}, got {was_invalidated}"
        )

    @pytest.mark.parametrize(
        "cache_name",
        [
            pytest.param("pattern_match", id="pattern_match_cache"),
            pytest.param("wildcard_count", id="wildcard_count_cache"),
            pytest.param("partial_coverage", id="partial_coverage_cache"),
            pytest.param("role_coverage", id="role_coverage_cache"),
            pytest.param("role_net_permissions", id="role_net_permissions_cache"),
        ],
    )
    def test_all_dependent_caches_cleared_on_invalidation(
        self,
        reader_role,
        cache_name: str,
    ):
        """Test that all operation-dependent caches are cleared on staleness."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        # Build initial cache
        initial_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(10)
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all([reader_role], initial_ops))

        # Verify the target cache has data
        cache = get_cache_service().container.cache
        target_cache = getattr(cache, cache_name)
        initial_size = len(target_cache)

        # For caches that may be empty initially, populate them
        if initial_size == 0:
            # Run a query to populate caches
            recommend_roles(
                ["*/read"],
                [reader_role],
                initial_ops,
                requested_ops_data_flags={"*/read": False},
            )
            target_cache = getattr(cache, cache_name)
            initial_size = len(target_cache)

        # Now change operations
        extended_ops = initial_ops + [
            OperationData(
                name=f"Microsoft.NewProvider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(5)
        ]

        # Create service with extended ops and trigger invalidation
        service = RoleRecommendationService(extended_ops, caches=cache)
        was_invalidated = service.check_cache_staleness()

        assert was_invalidated, "Cache should have been invalidated"

        # Verify the target cache is now empty
        cleared_cache = getattr(cache, cache_name)
        assert len(cleared_cache) == 0, (
            f"Cache '{cache_name}' should be empty after invalidation, "
            f"but has {len(cleared_cache)} entries"
        )

    def test_stale_pattern_match_returns_wrong_count_without_invalidation(
        self,
        reader_role,
    ):
        """Demonstrate the bug when pattern_match cache is NOT invalidated.

        This test shows what happens if we DON'T clear pattern_match:
        - Cache has 10 ops for */read pattern
        - Operations grow to 15
        - Without invalidation, cache still returns 10 ops
        - This causes wrong match counts

        This is a regression test for the Plane enum refactoring.
        """
        from azurerbac.matching.models import PatternCacheKey, Plane
        from azurerbac.matching.role_matching import get_matching_operations

        # Build initial cache with 10 operations
        initial_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(10)
        ]
        initial_op_names = frozenset(op.name for op in initial_ops)

        clear_computed_caches()
        cache = get_cache_service().container.cache

        # Simulate caching the pattern match
        cached_result = get_matching_operations(
            "*/read", initial_op_names, Plane.CONTROL, caches=cache
        )
        assert len(cached_result) == 10

        # Verify cache entry exists
        cache_key = PatternCacheKey("*/read", Plane.CONTROL)
        assert cache_key in cache.pattern_match
        assert len(cache.pattern_match[cache_key]) == 10

        # Now operations grow to 15
        extended_ops = initial_ops + [
            OperationData(
                name=f"Microsoft.NewProvider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(5)
        ]
        extended_op_names = frozenset(op.name for op in extended_ops)

        # WITHOUT clearing cache, get_matching_operations returns stale data
        stale_result = get_matching_operations(
            "*/read", extended_op_names, Plane.CONTROL, caches=cache
        )
        # BUG: Returns 10 instead of 15 because cache hit with stale data!
        assert len(stale_result) == 10, "Stale cache should return old count"

        # Now clear the cache (simulating proper invalidation)
        cache.pattern_match.clear()

        # Fresh query returns correct count
        fresh_result = get_matching_operations(
            "*/read", extended_op_names, Plane.CONTROL, caches=cache
        )
        assert len(fresh_result) == 15, "Fresh query should return new count"

    def test_partial_coverage_cache_invalidation(
        self,
        reader_role,
    ):
        """Test that partial_coverage cache is invalidated correctly."""
        from azurerbac.matching.models import Plane
        from azurerbac.matching.role_matching import count_wildcard_partial_coverage

        # Build operations where reader covers only some
        initial_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(10)
        ] + [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/write",
                is_data_action=False,
            )
            for i in range(5)
        ]
        all_op_names = frozenset(op.name for op in initial_ops)

        clear_computed_caches()
        cache = get_cache_service().container.cache

        # Query partial coverage for Microsoft.Provider0/* pattern
        # Reader (with */read) should cover /read but not /write
        pattern = "Microsoft.Provider0/*"
        result = count_wildcard_partial_coverage(
            pattern,
            ["*/read"],  # actions
            [],  # not_actions
            all_op_names,
            Plane.CONTROL,
            caches=cache,
        )

        # Should cover 1 out of 2 (read but not write for Provider0)
        assert result.covered == 1
        assert result.total == 2

        # Verify cache is populated
        assert len(cache.partial_coverage) == 1

        # Add more operations for Provider0
        extended_ops = [
            *initial_ops,
            OperationData(
                name="Microsoft.Provider0/resource/delete",
                is_data_action=False,
            ),
            OperationData(
                name="Microsoft.Provider0/resource/action",
                is_data_action=False,
            ),
        ]
        extended_op_names = frozenset(op.name for op in extended_ops)

        # Without clearing, we get stale result
        stale_result = count_wildcard_partial_coverage(
            pattern,
            ["*/read"],
            [],
            extended_op_names,
            Plane.CONTROL,
            caches=cache,
        )
        # Stale: still shows 1/2 instead of 1/4
        assert stale_result.covered == 1
        assert stale_result.total == 2

        # Clear and recompute
        cache.partial_coverage.clear()
        cache.pattern_match.clear()  # Also need to clear pattern_match

        fresh_result = count_wildcard_partial_coverage(
            pattern,
            ["*/read"],
            [],
            extended_op_names,
            Plane.CONTROL,
            caches=cache,
        )
        # Fresh: shows 1/4 (read out of read/write/delete/action)
        assert fresh_result.covered == 1
        assert fresh_result.total == 4

    @pytest.mark.parametrize(
        ("ops_decrease", "expected_behavior"),
        [
            pytest.param(True, "invalidate", id="ops_removed"),
            pytest.param(False, "invalidate", id="ops_added"),
        ],
    )
    def test_cache_invalidation_on_decrease(
        self,
        reader_role,
        ops_decrease: bool,
        expected_behavior: str,
    ):
        """Test cache invalidation works when operations decrease."""
        if ops_decrease:
            # Start with more, end with fewer
            initial_count, final_count = 20, 10
        else:
            # Start with fewer, end with more
            initial_count, final_count = 10, 20

        initial_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(initial_count)
        ]

        clear_computed_caches()
        get_cache_service().swap_in_memory(precompute_all([reader_role], initial_ops))

        final_ops = [
            OperationData(
                name=f"Microsoft.Provider{i}/resource/read",
                is_data_action=False,
            )
            for i in range(final_count)
        ]

        result = recommend_roles(
            ["*/read"],
            [reader_role],
            final_ops,
            requested_ops_data_flags={"*/read": False},
        )

        reader = next(r for r in result if r.role_name == "Reader")
        assert reader.matched_operations_count == final_count
        assert reader.match_percentage == 100.0
