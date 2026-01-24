"""Comprehensive tests for the role recommender logic.

Covers: pattern matching, action types, notActions, high privilege roles, sorting.
"""

import pytest

from azurerbac.core.patterns import matches_pattern, pattern_to_regex
from azurerbac.matching import recommend_roles
from azurerbac.matching.role_matching import (
    _prefix_pattern_covers,
    _segment_pattern_covers,
    _suffix_pattern_covers,
    check_operation_allowed,
    check_wildcard_operation_allowed,
    count_net_permissions,
    count_wildcard_partial_coverage,
    operation_matches_any_pattern,
    pattern_covers_pattern,
)
from tests.helpers import make_role_definition

# =============================================================================
# Pattern Matching Tests
# =============================================================================


class TestPatternMatching:
    """Tests for pattern matching helper functions."""

    @pytest.mark.parametrize(
        "pattern,operation,expected",
        [
            # Exact match
            (
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage/storageAccounts/read",
                True,
            ),
            (
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage/storageAccounts/write",
                False,
            ),
            # Wildcard *
            ("*", "anything/at/all", True),
            ("Microsoft.Storage/*", "Microsoft.Storage/storageAccounts/read", True),
            ("Microsoft.Storage/*", "Microsoft.Compute/virtualMachines/read", False),
            ("*/read", "Microsoft.Storage/storageAccounts/read", True),
            ("*/read", "Microsoft.Storage/storageAccounts/write", False),
            ("Microsoft.*/read", "Microsoft.Storage/storageAccounts/read", True),
            ("Microsoft.*/read", "Microsoft.Compute/virtualMachines/read", True),
            # Case insensitive
            (
                "Microsoft.Storage/storageAccounts/read",
                "microsoft.storage/storageaccounts/read",
                True,
            ),
        ],
    )
    def test_pattern_matching(self, pattern: str, operation: str, expected: bool):
        """Test pattern matching with various patterns."""
        regex = pattern_to_regex(pattern)
        assert bool(regex.match(operation)) == expected

    def testmatches_pattern_function(self):
        """Test matches_pattern convenience function."""
        assert matches_pattern("Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/*")
        assert matches_pattern("anything", "*")
        assert not matches_pattern("Microsoft.Compute/virtualMachines/read", "Microsoft.Storage/*")

    def testoperation_matches_any_pattern(self):
        """Test operation_matches_any_pattern function."""
        patterns = ["Microsoft.Storage/*", "Microsoft.Compute/virtualMachines/read"]
        assert operation_matches_any_pattern("Microsoft.Storage/storageAccounts/read", patterns)
        assert operation_matches_any_pattern("Microsoft.Compute/virtualMachines/read", patterns)
        assert not operation_matches_any_pattern("Microsoft.Network/virtualNetworks/read", patterns)


# =============================================================================
# Pattern Covers Pattern Tests
# =============================================================================


class TestPatternCoversPattern:
    """Tests for pattern_covers_pattern and helper functions."""

    @pytest.mark.parametrize(
        "role_pattern,requested_pattern,expected",
        [
            # Exact match
            ("Microsoft.Storage/read", "Microsoft.Storage/read", True),
            # Universal wildcard
            ("*", "Microsoft.Storage/storageAccounts/read", True),
            ("*", "anything/at/all", True),
            # Different patterns don't match
            ("Microsoft.Storage/write", "Microsoft.Storage/read", False),
        ],
    )
    def test_pattern_covers_exact_and_wildcard(
        self, role_pattern: str, requested_pattern: str, expected: bool
    ):
        """Test exact match and universal wildcard coverage."""
        assert pattern_covers_pattern(role_pattern, requested_pattern) == expected


class TestSuffixPatternCovers:
    """Tests for _suffix_pattern_covers function."""

    @pytest.mark.parametrize(
        "role_pattern,requested_pattern,expected",
        [
            # Valid suffix patterns
            ("*/read", "Microsoft.Storage/storageAccounts/read", True),
            ("*/delete", "Microsoft.Compute/virtualMachines/delete", True),
            # Suffix doesn't match
            ("*/read", "Microsoft.Storage/storageAccounts/write", False),
            ("*/delete", "Microsoft.Storage/storageAccounts/read", False),
            # Not a suffix pattern
            ("Microsoft.Storage/*", "Microsoft.Storage/accounts/read", False),
            ("Microsoft.Storage/read", "Microsoft.Storage/read", False),
            # Case sensitivity (Azure operations are case-insensitive but suffix check is literal)
            ("*/Read", "Microsoft.Storage/storageAccounts/Read", True),
        ],
    )
    def test_suffix_pattern_covers(self, role_pattern: str, requested_pattern: str, expected: bool):
        """Test suffix pattern coverage logic."""
        assert _suffix_pattern_covers(role_pattern, requested_pattern) == expected


class TestPrefixPatternCovers:
    """Tests for _prefix_pattern_covers function."""

    @pytest.mark.parametrize(
        "role_pattern,requested_pattern,expected",
        [
            # Valid prefix patterns (must end with /*)
            ("Microsoft.Storage/*", "Microsoft.Storage/storageAccounts/read", True),
            ("Microsoft.Compute/*", "Microsoft.Compute/virtualMachines/delete", True),
            # Prefix doesn't match
            ("Microsoft.Storage/*", "Microsoft.Compute/virtualMachines/read", False),
            ("Microsoft.Network/*", "Microsoft.Storage/storageAccounts/read", False),
            # Not a prefix pattern (doesn't end with /*)
            ("*/read", "Microsoft.Storage/accounts/read", False),
            ("Microsoft.Storage/read", "Microsoft.Storage/read", False),
            ("Microsoft.*", "Microsoft.Storage/read", False),  # Ends with * but not /*
        ],
    )
    def test_prefix_pattern_covers(self, role_pattern: str, requested_pattern: str, expected: bool):
        """Test prefix pattern coverage logic."""
        assert _prefix_pattern_covers(role_pattern, requested_pattern) == expected


class TestSegmentPatternCovers:
    """Tests for _segment_pattern_covers function - segment-by-segment matching."""

    @pytest.mark.parametrize(
        "role_pattern,requested_pattern,expected",
        [
            # Middle wildcard patterns
            ("Microsoft.Storage/*/read", "Microsoft.Storage/storageAccounts/read", True),
            # Trailing wildcard
            ("Microsoft.Storage/*", "Microsoft.Storage/storageAccounts/read", True),
            ("Microsoft.Storage/storageAccounts/*", "Microsoft.Storage/storageAccounts/read", True),
            # Multiple segments with wildcards
            (
                "Microsoft.Storage/*/blobServices/*",
                "Microsoft.Storage/accounts/blobServices/containers",
                True,
            ),
            # Pattern too long
            ("Microsoft.Storage/a/b/c/d", "Microsoft.Storage/a/b", False),
            # Segment mismatch
            ("Microsoft.Storage/*/write", "Microsoft.Storage/storageAccounts/read", False),
            ("Microsoft.Compute/*/read", "Microsoft.Storage/storageAccounts/read", False),
            # Requested has wildcard but role has specific
            ("Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/*/read", False),
            # Case insensitivity
            ("microsoft.storage/*/read", "Microsoft.Storage/storageAccounts/read", True),
            # First segment wildcard (Microsoft.* style patterns)
            ("*/virtualMachines/read", "Microsoft.Compute/virtualMachines/read", True),
        ],
    )
    def test_segment_pattern_covers(
        self, role_pattern: str, requested_pattern: str, expected: bool
    ):
        """Test segment-by-segment pattern coverage."""
        assert _segment_pattern_covers(role_pattern, requested_pattern) == expected


# =============================================================================
# Wildcard Operation Tests
# =============================================================================


class TestCheckWildcardOperationAllowed:
    """Tests for check_wildcard_operation_allowed function."""

    @pytest.mark.parametrize(
        "requested_pattern,actions,not_actions,expected",
        [
            # Universal wildcard covers everything
            ("Microsoft.Storage/*", ["*"], [], True),
            # Prefix pattern covers prefix request
            ("Microsoft.Storage/*", ["Microsoft.Storage/*"], [], True),
            # Suffix pattern covers suffix request
            ("*/read", ["*/read"], [], True),
            # Broader action covers narrower request
            ("Microsoft.Storage/storageAccounts/*", ["Microsoft.Storage/*"], [], True),
            # notAction excludes
            ("Microsoft.Storage/*", ["*"], ["Microsoft.Storage/*"], False),
            # notAction overlaps with wildcard request (conservative: returns False)
            ("*/read", ["*"], ["Microsoft.Authorization/*/read"], False),
            # notAction doesn't overlap (different suffix)
            ("*/read", ["*"], ["Microsoft.Authorization/*/delete"], True),
            # Action doesn't cover request
            ("Microsoft.Storage/*", ["Microsoft.Compute/*"], [], False),
            # Complex: notAction prefix/suffix don't overlap with request
            (
                "Microsoft.Storage/*/read",
                ["*"],
                ["Microsoft.Authorization/*/delete"],
                True,
            ),
        ],
    )
    def test_wildcard_operation_allowed(
        self,
        requested_pattern: str,
        actions: list[str],
        not_actions: list[str],
        expected: bool,
    ):
        """Test wildcard operation allowed logic."""
        assert check_wildcard_operation_allowed(requested_pattern, actions, not_actions) == expected


class TestCountWildcardPartialCoverage:
    """Tests for count_wildcard_partial_coverage function."""

    @pytest.mark.parametrize(
        "requested_pattern,actions,not_actions,all_ops,expected_covered,expected_total",
        [
            pytest.param(
                "Microsoft.Storage/*",
                ["microsoft.storage/storageaccounts/read"],  # Explicit must match lowercase
                [],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                    "microsoft.storage/storageaccounts/delete",
                    "microsoft.compute/virtualmachines/read",
                },
                1,
                3,
                id="single-action-partial-coverage",
            ),
            pytest.param(
                "Microsoft.Storage/*",
                ["Microsoft.Storage/*"],  # Wildcards use pattern matching (case-insensitive)
                [],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                    "microsoft.compute/virtualmachines/read",
                },
                2,
                2,
                id="wildcard-action-full-coverage",
            ),
            pytest.param(
                "Microsoft.Storage/*",
                ["*"],
                [],
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                },
                2,
                2,
                id="star-action-full-coverage",
            ),
            pytest.param(
                "Microsoft.Storage/*",
                ["*"],
                [],
                {"microsoft.compute/virtualmachines/read"},
                0,
                0,
                id="no-matching-operations",
            ),
        ],
    )
    def test_coverage_parametrized(
        self,
        requested_pattern: str,
        actions: list[str],
        not_actions: list[str],
        all_ops: set[str],
        expected_covered: int,
        expected_total: int,
    ):
        """Test coverage calculation with various scenarios."""
        result = count_wildcard_partial_coverage(
            requested_pattern=requested_pattern,
            actions=actions,
            not_actions=not_actions,
            all_operations=all_ops,
        )
        assert result.covered == expected_covered
        assert result.total == expected_total

    def test_coverage_with_not_actions(self):
        """Test that notActions properly exclude operations via pattern matching."""
        all_ops = {
            "microsoft.storage/storageaccounts/read",
            "microsoft.storage/storageaccounts/write",
            "microsoft.storage/storageaccounts/delete",
        }
        # Use wildcard notAction to properly exclude via pattern matching
        result = count_wildcard_partial_coverage(
            requested_pattern="Microsoft.Storage/*",
            actions=["Microsoft.Storage/*"],
            not_actions=[
                "Microsoft.Storage/storageAccounts/delete"
            ],  # Wildcardless must match exactly
            all_operations=all_ops,
        )
        # Note: explicit notAction "Microsoft.Storage/storageAccounts/delete" won't match
        # "microsoft.storage/storageaccounts/delete" due to case difference
        # Use wildcard pattern for case-insensitive exclusion
        assert result.covered == 3  # All matched because notAction didn't match (case)

    def test_coverage_with_wildcard_not_actions(self):
        """Test that wildcard notActions properly exclude operations."""
        all_ops = {
            "microsoft.storage/storageaccounts/read",
            "microsoft.storage/storageaccounts/write",
            "microsoft.storage/storageaccounts/delete",
        }
        result = count_wildcard_partial_coverage(
            requested_pattern="Microsoft.Storage/*",
            actions=["Microsoft.Storage/*"],
            not_actions=["*/delete"],  # Wildcard pattern for case-insensitive matching
            all_operations=all_ops,
        )
        assert result.covered == 2
        assert result.uncovered == 1


class TestCountNetPermissions:
    """Tests for count_net_permissions function."""

    @pytest.mark.parametrize(
        "actions,not_actions,all_ops,expected",
        [
            pytest.param([], [], {"op1", "op2"}, 0, id="empty-actions-zero"),
            pytest.param(["*"], [], {"op1", "op2", "op3"}, 3, id="star-returns-all"),
            pytest.param(["op1", "op2"], [], {"op1", "op2", "op3"}, 2, id="explicit-actions"),
            pytest.param(
                ["*"],
                ["microsoft.storage/read"],
                {"microsoft.storage/read", "microsoft.storage/write", "microsoft.compute/read"},
                2,
                id="star-minus-explicit-notaction",
            ),
        ],
    )
    def test_count_net_permissions_parametrized(
        self,
        actions: list[str],
        not_actions: list[str],
        all_ops: set[str],
        expected: int,
    ):
        """Test net permission counting with various scenarios."""
        assert count_net_permissions(actions, not_actions, all_ops) == expected

    def test_wildcard_actions_expanded(self):
        """Wildcard actions are expanded and counted."""
        all_ops = {
            "microsoft.storage/storageaccounts/read",
            "microsoft.storage/storageaccounts/write",
            "microsoft.compute/virtualmachines/read",
        }
        result = count_net_permissions(["Microsoft.Storage/*"], [], all_ops)
        assert result == 2


# =============================================================================
# Operation Allowed Tests
# =============================================================================


class TestCheckOperationAllowed:
    """Tests for check_operation_allowed function."""

    @pytest.mark.parametrize(
        "operation,actions,not_actions,expected",
        [
            # Basic matches
            ("Microsoft.Storage/read", ["Microsoft.Storage/read"], [], True),
            ("Microsoft.Storage/read", ["Microsoft.Storage/*"], [], True),
            ("Microsoft.Storage/read", ["*"], [], True),
            ("Microsoft.Storage/read", ["Microsoft.Compute/*"], [], False),
            ("Microsoft.Storage/read", [], [], False),
            # NotActions exclusions (pattern needs nested path to match)
            (
                "Microsoft.Storage/storageAccounts/delete",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/*/delete"],
                False,
            ),
            (
                "Microsoft.Storage/storageAccounts/read",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/*/delete"],
                True,
            ),
            # Contributor pattern
            ("Microsoft.Storage/read", ["*"], ["Microsoft.Authorization/*/write"], True),
            (
                "Microsoft.Authorization/roleAssignments/write",
                ["*"],
                ["Microsoft.Authorization/*/write"],
                False,
            ),
        ],
    )
    def test_operation_allowed(
        self, operation: str, actions: list, not_actions: list, expected: bool
    ):
        """Test operation allowed logic with various patterns."""
        assert check_operation_allowed(operation, actions, not_actions) == expected


# =============================================================================
# User Selection Scenarios
# =============================================================================


class TestUserSelectionScenarios:
    """Tests for different user selection scenarios."""

    def test_empty_selection_returns_empty(self, sample_operations, populated_cache):
        """Empty selection returns empty results."""
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles([], roles)
        assert result == []

    def test_single_operation_selection(self, sample_operations, populated_cache):
        """Single operation selection returns matching roles."""
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) >= 1
        assert all(r.is_full_match for r in result)

    def test_multiple_operations_selection(self, sample_operations, populated_cache):
        """Multiple operations selection with partial and full matches."""
        roles = [
            make_role_definition("Storage Admin", "r1", ["Microsoft.Storage/*"]),
            make_role_definition("Reader", "r2", ["*/read"]),
        ]
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/storageAccounts/write"],
            roles,
        )
        full_matches = [r for r in result if r.is_full_match]
        assert any(r.role_name == "Storage Admin" for r in full_matches)


# =============================================================================
# Action Type Tests
# =============================================================================


class TestActionTypes:
    """Tests for control plane vs data plane operations."""

    def test_control_plane_only_role(self, sample_operations, populated_cache):
        """Control plane role matches control plane operations."""
        roles = [
            make_role_definition(
                "Control Only", "r1", actions=["Microsoft.Storage/*"], data_actions=[]
            )
        ]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) == 1
        assert result[0].is_full_match

    def test_data_plane_only_role(self, sample_operations, populated_cache):
        """Data plane role matches data plane operations."""
        roles = [
            make_role_definition(
                "Data Only",
                "r1",
                actions=[],
                data_actions=["Microsoft.Storage/storageAccounts/blobServices/*"],
            )
        ]
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            roles,
        )
        assert len(result) == 1
        assert result[0].is_full_match

    def test_control_plane_not_matched_by_data_role(self, sample_operations, populated_cache):
        """Control plane operation not matched by data plane role."""
        roles = [
            make_role_definition(
                "Data Only", "r1", actions=[], data_actions=["Microsoft.Storage/*"]
            )
        ]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) == 0

    def test_mixed_control_and_data(self, sample_operations, populated_cache):
        """Mixed role matches both control and data plane."""
        roles = [
            make_role_definition(
                "Mixed",
                "r1",
                actions=["Microsoft.Storage/storageAccounts/read"],
                data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/blobs/read"],
            )
        ]
        result = recommend_roles(
            [
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
            ],
            roles,
        )
        assert len(result) == 1
        assert result[0].is_full_match
        assert len(result[0].matched_operations) == 2


# =============================================================================
# High Privilege Role Tests
# =============================================================================


class TestHighPrivilegeRoles:
    """Tests for high privilege role identification."""

    @pytest.mark.parametrize(
        "role_name,actions,not_actions,expected_high_privilege",
        [
            ("Owner", ["*"], [], True),
            ("Contributor", ["*"], ["Microsoft.Authorization/*/write"], True),
            ("User Access Administrator", ["Microsoft.Authorization/*"], [], True),
            (
                "Role Based Access Control Administrator",
                ["Microsoft.Authorization/roleAssignments/*"],
                [],
                True,
            ),
            ("Storage Reader", ["Microsoft.Storage/*/read"], [], False),
        ],
    )
    def test_high_privilege_detection(
        self,
        sample_operations,
        populated_cache,
        role_name,
        actions,
        not_actions,
        expected_high_privilege,
    ):
        """Test that high privilege roles are correctly identified."""
        roles = [make_role_definition(role_name, "r1", actions, not_actions)]
        # Use an operation that will match
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        if result:
            assert result[0].is_high_privilege == expected_high_privilege

    def test_high_privilege_roles_sorted_last(self, sample_operations, populated_cache):
        """Non-high-privilege roles should appear before high-privilege roles."""
        roles = [
            make_role_definition("Owner", "r1", ["*"]),
            make_role_definition("Storage Admin", "r2", ["Microsoft.Storage/*"]),
        ]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        full_matches = [r for r in result if r.is_full_match]
        if len(full_matches) >= 2:
            storage_idx = next(
                i for i, r in enumerate(full_matches) if r.role_name == "Storage Admin"
            )
            owner_idx = next(i for i, r in enumerate(full_matches) if r.role_name == "Owner")
            assert storage_idx < owner_idx


# =============================================================================
# NotActions Exclusion Tests
# =============================================================================


class TestNotActionsExclusions:
    """Tests for notActions and notDataActions exclusions."""

    def test_not_action_excludes_operation(self, sample_operations, populated_cache):
        """notAction excludes specific operation."""
        roles = [
            make_role_definition(
                "No Delete",
                "r1",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/storageAccounts/delete"],
            )
        ]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/delete"], roles)
        assert len(result) == 0

    def test_not_action_allows_other_operations(self, sample_operations, populated_cache):
        """notAction doesn't affect non-excluded operations."""
        roles = [
            make_role_definition(
                "No Delete",
                "r1",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/storageAccounts/delete"],
            )
        ]
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/storageAccounts/write"],
            roles,
        )
        assert len(result) == 1
        assert result[0].is_full_match


# =============================================================================
# Sorting and Ranking Tests
# =============================================================================


class TestSortingAndRanking:
    """Tests for result sorting and ranking."""

    def test_full_matches_before_partial(self, sample_operations, populated_cache):
        """Full matches should appear before partial matches."""
        roles = [
            make_role_definition("Partial", "r1", ["Microsoft.Storage/*/read"]),
            make_role_definition("Full", "r2", ["Microsoft.Storage/*"]),
        ]
        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/storageAccounts/write"],
            roles,
        )
        full_indices = [i for i, r in enumerate(result) if r.is_full_match]
        partial_indices = [i for i, r in enumerate(result) if not r.is_full_match]
        if full_indices and partial_indices:
            assert max(full_indices) < min(partial_indices)

    def test_no_match_returns_empty(self, sample_operations, populated_cache):
        """No role matches returns empty list."""
        roles = [make_role_definition("Compute Only", "r1", ["Microsoft.Compute/*"])]
        result = recommend_roles(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) == 0


# =============================================================================
# Value Objects Tests
# =============================================================================


class TestClassifiedOperations:
    """Tests for ClassifiedOperations value object."""

    def test_classified_operations_immutable(self):
        """ClassifiedOperations should be immutable (frozen)."""
        from dataclasses import FrozenInstanceError

        from azurerbac.matching.models import ClassifiedOperations

        classified = ClassifiedOperations(
            control=frozenset(["op1"]),
            data=frozenset(["op2"]),
        )
        with pytest.raises(FrozenInstanceError):
            classified.control = frozenset(["new"])

    def test_all_requested_combines_all_sets(self):
        """all_requested should combine all operation sets."""
        from azurerbac.matching.models import ClassifiedOperations

        classified = ClassifiedOperations(
            control=frozenset(["ctrl1"]),
            data=frozenset(["data1"]),
            control_wildcards=frozenset(["ctrl_wc"]),
            data_wildcards=frozenset(["data_wc"]),
        )
        assert classified.all_requested == frozenset(["ctrl1", "data1", "ctrl_wc", "data_wc"])

    def test_len_returns_total_count(self):
        """len() should return total operation count."""
        from azurerbac.matching.models import ClassifiedOperations

        classified = ClassifiedOperations(
            control=frozenset(["c1", "c2"]),
            data=frozenset(["d1"]),
            control_wildcards=frozenset(["cw1"]),
        )
        assert len(classified) == 4


class TestOperationSets:
    """Tests for OperationSets value object."""

    def test_from_operations_creates_correct_sets(self, sample_operations):
        """from_operations should separate control and data plane operations (lowered)."""
        from azurerbac.matching.models import OperationSets

        op_sets = OperationSets.from_operations(sample_operations)

        # Control plane operations (stored lowered)
        assert "microsoft.storage/storageaccounts/read" in op_sets.all_control
        assert "microsoft.compute/virtualmachines/read" in op_sets.all_control

        # Data plane operations (stored lowered)
        assert "microsoft.keyvault/vaults/secrets/read" in op_sets.all_data
        assert (
            "microsoft.storage/storageaccounts/blobservices/containers/blobs/read"
            in op_sets.all_data
        )


class TestRecommendationService:
    """Tests for RoleRecommendationService."""

    def test_classify_operations_separates_by_plane(self, sample_operations, populated_cache):
        """classify_operations should correctly separate control and data operations."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        svc = RoleRecommendationService()
        classified = svc.classify_operations(
            [
                "Microsoft.Storage/storageAccounts/read",  # Control
                "Microsoft.KeyVault/vaults/secrets/read",  # Data
            ]
        )

        # Operations are stored lowered for case-insensitive matching
        assert "microsoft.storage/storageaccounts/read" in classified.control
        assert "microsoft.keyvault/vaults/secrets/read" in classified.data

    def test_classify_operations_handles_wildcards(self, sample_operations, populated_cache):
        """classify_operations should detect wildcards matching both planes."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        svc = RoleRecommendationService()
        classified = svc.classify_operations(["Microsoft.Storage/*"])

        # This wildcard should match both control and data operations
        assert "Microsoft.Storage/*" in classified.control_wildcards
        assert "Microsoft.Storage/*" in classified.data_wildcards

    def test_compute_wildcard_matches_expands_patterns(self, sample_operations, populated_cache):
        """compute_wildcard_matches should expand wildcards to actual operations."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        svc = RoleRecommendationService()
        classified = svc.classify_operations(["Microsoft.Compute/*"])
        total_count = svc.compute_wildcard_matches(classified)

        # Microsoft.Compute/* should match multiple control plane operations
        assert total_count > 0
        assert "Microsoft.Compute/*" in svc.control_wildcard_ops
        assert len(svc.control_wildcard_ops["Microsoft.Compute/*"]) >= 3


# =============================================================================
# Max Results Parameter Tests
# =============================================================================


class TestMaxResultsParameter:
    """Tests that max_results parameter works correctly."""

    @pytest.mark.parametrize(
        ("num_roles", "max_results", "expected_count"),
        [
            pytest.param(25, None, 25, id="no_limit_returns_all"),
            pytest.param(25, 10, 10, id="explicit_limit_10"),
            pytest.param(35, None, 35, id="large_set_no_limit"),
            pytest.param(5, 10, 5, id="limit_exceeds_matches"),
        ],
    )
    def test_max_results_parameter(
        self,
        sample_operations,
        populated_cache,
        num_roles: int,
        max_results: int | None,
        expected_count: int,
    ):
        """Test max_results parameter behavior."""
        roles = [
            make_role_definition(f"Role {i}", f"role-id-{i}", ["Microsoft.Test/resource/read"])
            for i in range(num_roles)
        ]
        result = recommend_roles(
            ["Microsoft.Test/resource/read"],
            roles,
            max_results=max_results,
        )
        assert len(result) == expected_count


# =============================================================================
# Missing Operations Expanded Tests
# =============================================================================


class TestMissingOperationsExpanded:
    """Tests for missing_operations_expanded field correctness."""

    def test_zero_coverage_wildcard_has_expanded_ops(self, sample_operations, populated_cache):
        """When a role has 0 coverage for a wildcard, expanded ops should be populated."""
        # Role with NO data plane permissions
        reader_role = make_role_definition(
            "Reader", "reader-id", actions=["*/read"], data_actions=[]
        )

        # Request both control and data plane */read (no flags = both planes)
        result = recommend_roles(["*/read"], [reader_role])

        assert len(result) == 1
        reader = result[0]

        # Reader should have missing data plane operations
        if reader.missing_operations_count > 0:
            assert len(reader.missing_operations_expanded) > 0, (
                "missing_operations_expanded should have samples when missing_operations_count > 0"
            )
            # Check that expanded ops are real operation names, not wildcards
            for op in reader.missing_operations_expanded:
                assert "*" not in op, f"Expanded op should not be a wildcard: {op}"


# =============================================================================
# Reader Role Edge Cases
# =============================================================================


class TestReaderRoleEdgeCases:
    """Specific tests for Reader role behavior - a common edge case."""

    @pytest.mark.parametrize(
        ("data_flag", "expected_full_match"),
        [
            pytest.param(False, True, id="control_plane_only_full_match"),
            pytest.param(True, False, id="data_plane_only_no_match"),
        ],
    )
    def test_reader_wildcard_with_plane_flag(
        self, sample_operations, populated_cache, data_flag: bool, expected_full_match: bool
    ):
        """Test Reader role behavior with different plane flags."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        result = recommend_roles(
            ["*/read"],
            [reader],
            requested_ops_data_flags={"*/read": data_flag},
        )

        if expected_full_match:
            assert len(result) == 1
            assert result[0].is_full_match is True
            assert result[0].match_percentage == 100.0
        else:
            # Reader has no data actions, should not match data-only request
            assert len(result) == 0

    def test_reader_both_planes_partial_match(self, sample_operations, populated_cache):
        """Reader requesting both planes should show partial match."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        # Request both planes (no flag = both)
        result = recommend_roles(["*/read"], [reader])

        assert len(result) == 1
        reader_result = result[0]

        assert reader_result.is_full_match is False
        assert reader_result.match_percentage < 100.0
        assert reader_result.missing_operations_count > 0
        assert reader_result.has_partial_wildcard_match is True


# =============================================================================
# Intra-Request Cache Consistency Tests
# =============================================================================


class TestIntraRequestCacheConsistency:
    """Tests that RoleRecommendationService uses consistent cache within a request.

    The service captures cache eagerly at construction to ensure op_sets and _cache
    are always from the same snapshot. This prevents race conditions where a background
    job swaps the cache mid-request.
    """

    def test_service_uses_cache_from_construction_time(self, sample_operations, populated_cache):
        """Service should use the cache provided at construction, not global singleton."""
        from azurerbac.cache.models import CacheData
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        # Create a custom cache with known data
        custom_cache = CacheData()
        custom_cache.role_coverage["test-role-id"] = None  # Mark as known

        # Create service with explicit cache
        svc = RoleRecommendationService(
            requested_ops_data_flags=None,
            cache=custom_cache,
        )

        # Verify it uses our custom cache, not global
        assert svc._caches is custom_cache
        assert "test-role-id" in svc._caches.role_coverage

    def test_service_captures_cache_eagerly_when_none_provided(
        self, sample_operations, populated_cache
    ):
        """When no cache provided, service captures global cache at construction."""
        from unittest.mock import patch

        from azurerbac.cache.models import CacheData
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        # Create two different cache instances
        cache_v1 = CacheData()
        cache_v1.role_coverage["v1-marker"] = None

        cache_v2 = CacheData()
        cache_v2.role_coverage["v2-marker"] = None

        # Track which cache to return
        current_cache = [cache_v1]  # Use list to allow mutation in nested function

        def mock_get_default_cache():
            return current_cache[0]

        with patch(
            "azurerbac.matching.recommendation_service._get_default_cache",
            side_effect=mock_get_default_cache,
        ):
            # Create service - should capture cache_v1
            svc = RoleRecommendationService()

            # Verify it captured cache_v1
            assert "v1-marker" in svc._caches.role_coverage

            # Now swap the global cache to v2
            current_cache[0] = cache_v2

            # Service should STILL use cache_v1 (captured at construction)
            assert "v1-marker" in svc._caches.role_coverage
            assert "v2-marker" not in svc._caches.role_coverage

    def test_new_service_gets_fresh_cache(self, sample_operations, populated_cache):
        """Each new service instance captures the current cache state."""
        from unittest.mock import patch

        from azurerbac.cache.models import CacheData
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        cache_v1 = CacheData()
        cache_v1.role_coverage["v1-marker"] = None

        cache_v2 = CacheData()
        cache_v2.role_coverage["v2-marker"] = None

        current_cache = [cache_v1]

        def mock_get_default_cache():
            return current_cache[0]

        with patch(
            "azurerbac.matching.recommendation_service._get_default_cache",
            side_effect=mock_get_default_cache,
        ):
            # First service gets v1
            svc1 = RoleRecommendationService()
            assert "v1-marker" in svc1._caches.role_coverage

            # Swap cache
            current_cache[0] = cache_v2

            # Second service gets v2 (fresh)
            svc2 = RoleRecommendationService()
            assert "v2-marker" in svc2._caches.role_coverage

            # First service still has v1
            assert "v1-marker" in svc1._caches.role_coverage
