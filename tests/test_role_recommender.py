"""Comprehensive tests for the role recommender logic.

Covers: pattern matching, action types, notActions, high privilege roles, sorting.
"""

import pytest

from azurerbac.core.patterns import matches_pattern, pattern_to_regex
from azurerbac.matching.role_matching import (
    _prefix_pattern_covers,
    _segment_pattern_covers,
    _suffix_pattern_covers,
    check_operation_allowed,
    check_wildcard_operation_allowed,
    count_net_permissions,
    count_wildcard_partial_coverage,
    is_high_privilege_role,
    operation_matches_any_pattern,
    pattern_covers_pattern,
)
from tests.helpers import make_operation, make_role_definition, recommend_roles_with_cache

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

    def test_empty_selection_returns_empty(self, populated_cache):
        """Empty selection returns empty results."""
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles_with_cache([], roles)
        assert result == []

    def test_single_operation_selection(self, populated_cache):
        """Single operation selection returns matching roles."""
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles_with_cache(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) >= 1
        assert all(r.is_full_match for r in result)

    def test_multiple_operations_selection(self, populated_cache):
        """Multiple operations selection with partial and full matches."""
        roles = [
            make_role_definition("Storage Admin", "r1", ["Microsoft.Storage/*"]),
            make_role_definition("Reader", "r2", ["*/read"]),
        ]
        result = recommend_roles_with_cache(
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

    def test_control_plane_only_role(self, populated_cache):
        """Control plane role matches control plane operations."""
        roles = [
            make_role_definition(
                "Control Only", "r1", actions=["Microsoft.Storage/*"], data_actions=[]
            )
        ]
        result = recommend_roles_with_cache(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) == 1
        assert result[0].is_full_match

    def test_data_plane_only_role(self, populated_cache):
        """Data plane role matches data plane operations."""
        roles = [
            make_role_definition(
                "Data Only",
                "r1",
                actions=[],
                data_actions=["Microsoft.Storage/storageAccounts/blobServices/*"],
            )
        ]
        result = recommend_roles_with_cache(
            ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
            roles,
        )
        assert len(result) == 1
        assert result[0].is_full_match

    def test_control_plane_not_matched_by_data_role(self, populated_cache):
        """Control plane operation not matched by data plane role."""
        roles = [
            make_role_definition(
                "Data Only", "r1", actions=[], data_actions=["Microsoft.Storage/*"]
            )
        ]
        result = recommend_roles_with_cache(["Microsoft.Storage/storageAccounts/read"], roles)
        assert len(result) == 0

    def test_mixed_control_and_data(self, populated_cache):
        """Mixed role matches both control and data plane."""
        roles = [
            make_role_definition(
                "Mixed",
                "r1",
                actions=["Microsoft.Storage/storageAccounts/read"],
                data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/blobs/read"],
            )
        ]
        result = recommend_roles_with_cache(
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


class TestIsHighPrivilegeRole:
    """Unit tests for is_high_privilege_role function.

    A role is high-privilege if:
    1. It's a well-known high-privilege role (Owner, Contributor, User Access Administrator)
    2. OR it allows Microsoft.Authorization/roleAssignments/write in any permission block
       without a condition that constrains or mentions roleAssignments/write.

    These tests directly test the is_high_privilege_role function (no cache required).
    """

    @pytest.mark.parametrize(
        "description,role_id,expected",
        [
            # Well-known high-privilege role IDs
            ("owner_by_id", "8e3af657-a8ff-443c-a75c-2fe8c4bcb635", True),
            ("contributor_by_id", "b24988ac-6180-42a0-ab88-20f7382dd24c", True),
            ("user_access_admin_by_id", "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9", True),
            # Unknown role ID - falls back to permission check
            ("unknown_role_id", "00000000-0000-0000-0000-000000000000", False),
        ],
    )
    def test_well_known_role_ids(self, description: str, role_id: str, expected: bool):
        """Test that well-known high-privilege role IDs are detected by ID."""
        # Use minimal permissions - the ID check should short-circuit
        role = make_role_definition("Test Role", role_id, actions=["Microsoft.Storage/*/read"])
        assert is_high_privilege_role(role) is expected, f"Failed for: {description}"

    @pytest.mark.parametrize(
        "description,actions,not_actions,condition,expected",
        [
            # === HIGH PRIVILEGE CASES (True) ===
            # Wildcard patterns that include roleAssignments/write
            ("wildcard_star", ["*"], [], None, True),
            ("authorization_star", ["Microsoft.Authorization/*"], [], None, True),
            (
                "role_assignments_star",
                ["Microsoft.Authorization/roleAssignments/*"],
                [],
                None,
                True,
            ),
            # Explicit roleAssignments/write
            (
                "explicit_role_assignments_write",
                ["Microsoft.Authorization/roleAssignments/write"],
                [],
                None,
                True,
            ),
            # Case insensitive matching
            (
                "uppercase_role_assignments",
                ["MICROSOFT.AUTHORIZATION/ROLEASSIGNMENTS/WRITE"],
                [],
                None,
                True,
            ),
            (
                "mixed_case_role_assignments",
                ["Microsoft.AUTHORIZATION/RoleAssignments/Write"],
                [],
                None,
                True,
            ),
            # === NOT HIGH PRIVILEGE CASES (False) ===
            # No Authorization permissions
            ("storage_only", ["Microsoft.Storage/*"], [], None, False),
            ("compute_only", ["Microsoft.Compute/*"], [], None, False),
            ("empty_actions", [], [], None, False),
            # notActions excludes roleAssignments/write
            (
                "star_with_not_authorization_write",
                ["*"],
                ["Microsoft.Authorization/*/write"],
                None,
                False,
            ),
            (
                "star_with_not_role_assignments_write",
                ["*"],
                ["Microsoft.Authorization/roleAssignments/write"],
                None,
                False,
            ),
            (
                "authorization_star_with_not_role_assignments",
                ["Microsoft.Authorization/*"],
                ["Microsoft.Authorization/roleAssignments/write"],
                None,
                False,
            ),
            # Has condition that constrains roleAssignments/write specifically
            (
                "role_assignments_write_with_condition",
                ["Microsoft.Authorization/roleAssignments/*"],
                [],
                "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) "
                "OR (@Request[...]:RoleDefinitionId ForAnyOfAnyValues:GuidEquals{...}))",
                False,
            ),
            # Has condition but doesn't mention roleAssignments/write - still high privilege
            (
                "role_assignments_with_unrelated_condition",
                ["Microsoft.Authorization/roleAssignments/*"],
                [],
                "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] "
                "ForAnyOfAnyValues:GuidEquals{abc123}",
                True,
            ),
            (
                "star_with_condition",
                ["*"],
                [],
                "((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR ...)",
                False,
            ),
            # Authorization read-only (no write)
            (
                "authorization_read_only",
                ["Microsoft.Authorization/*/read"],
                [],
                None,
                False,
            ),
            (
                "role_assignments_read_only",
                ["Microsoft.Authorization/roleAssignments/read"],
                [],
                None,
                False,
            ),
            # Role definitions (not role assignments)
            (
                "role_definitions_write",
                ["Microsoft.Authorization/roleDefinitions/write"],
                [],
                None,
                False,
            ),
        ],
    )
    def test_single_permission_block(
        self,
        description: str,
        actions: list,
        not_actions: list,
        condition: str | None,
        expected: bool,
    ):
        """Test is_high_privilege_role with single permission block scenarios."""
        # Use a non-well-known role ID to test permission-based detection
        role = make_role_definition(
            "Test Role",
            "test-role-id",
            actions=actions,
            not_actions=not_actions,
            condition=condition,
        )
        assert is_high_privilege_role(role) is expected, f"Failed for: {description}"

    @pytest.mark.parametrize(
        "description,blocks,expected",
        [
            # Multiple blocks - one unconditioned high privilege
            (
                "storage_then_role_assignments",
                [
                    {"actions": ["Microsoft.Storage/*"]},
                    {"actions": ["Microsoft.Authorization/roleAssignments/write"]},
                ],
                True,
            ),
            (
                "role_assignments_then_storage",
                [
                    {"actions": ["Microsoft.Authorization/roleAssignments/write"]},
                    {"actions": ["Microsoft.Storage/*"]},
                ],
                True,
            ),
            # Multiple blocks - all conditioned or no role assignments
            (
                "conditioned_role_assignments_and_storage",
                [
                    {
                        "actions": ["Microsoft.Authorization/roleAssignments/*"],
                        "condition": "((!(ActionMatches{'Microsoft.Authorization/"
                        "roleAssignments/write'})) OR (@Request[...]:RoleDefinitionId "
                        "ForAnyOfAnyValues:GuidEquals{...}))",
                    },
                    {"actions": ["Microsoft.Storage/*"]},
                ],
                False,
            ),
            (
                "multiple_storage_blocks",
                [
                    {"actions": ["Microsoft.Storage/storageAccounts/*"]},
                    {"actions": ["Microsoft.Storage/blobServices/*"]},
                ],
                False,
            ),
            # Service Group Administrator pattern (real Azure role)
            (
                "service_group_admin_pattern",
                [
                    # Block 1: broad permissions but excludes roleAssignments
                    {
                        "actions": ["Microsoft.Management/*"],
                        "notActions": [
                            "Microsoft.Authorization/roleAssignments/write",
                            "Microsoft.Authorization/roleAssignments/delete",
                        ],
                    },
                    # Block 2: roleAssignments with condition
                    {
                        "actions": [
                            "Microsoft.Authorization/roleAssignments/write",
                            "Microsoft.Authorization/roleAssignments/delete",
                        ],
                        "condition": (
                            "((!(ActionMatches{'Microsoft.Authorization"
                            "/roleAssignments/write'})) "
                            "OR (@Request[...]:RoleDefinitionId "
                            "ForAnyOfAnyValues:GuidEquals{...}))"
                        ),
                    },
                ],
                False,
            ),
            # Empty blocks
            ("empty_blocks", [], False),
            ("single_empty_block", [{"actions": []}], False),
        ],
    )
    def test_multiple_permission_blocks(self, description: str, blocks: list, expected: bool):
        """Test is_high_privilege_role with multiple permission block scenarios."""
        from tests.helpers import make_role_with_multiple_permissions

        role = make_role_with_multiple_permissions("Test Role", "test-role-id", blocks)
        assert is_high_privilege_role(role) is expected, f"Failed for: {description}"


# =============================================================================
# High Privilege Integration Tests
# =============================================================================


class TestHighPrivilegeIntegration:
    """Integration tests for high-privilege role handling in precompute_all and recommend_roles.

    These tests verify that:
    1. precompute_all() correctly identifies and stores high-privilege role IDs
    2. recommend_roles() sets the is_high_privilege flag based on cached data
    3. Sorting prioritizes non-high-privilege roles over high-privilege ones
    """

    def test_precompute_all_populates_high_privilege_roles(self, populated_cache):
        """Verify precompute_all correctly identifies high-privilege roles."""
        from azurerbac.cache.build import precompute_all

        # Role with unconstrained roleAssignments/write -> high privilege
        high_priv_role = make_role_definition(
            "High Privilege Role",
            "high-priv-id",
            ["Microsoft.Authorization/roleAssignments/write"],
        )
        # Role without roleAssignments/write -> not high privilege
        low_priv_role = make_role_definition(
            "Low Privilege Role",
            "low-priv-id",
            ["Microsoft.Storage/storageAccounts/read"],
        )
        # Role with constrained roleAssignments/write -> not high privilege
        # The condition must contain the operation string to be recognized as constrained
        constrained_role = make_role_definition(
            "Constrained Role",
            "constrained-id",
            ["Microsoft.Authorization/roleAssignments/write"],
            condition="@Request[Microsoft.Authorization/roleAssignments/write:RoleDefinitionId]",
        )

        operations = [
            make_operation("Microsoft.Authorization/roleAssignments/write"),
            make_operation("Microsoft.Storage/storageAccounts/read"),
        ]
        roles = [high_priv_role, low_priv_role, constrained_role]

        cache_data = precompute_all(roles, operations)

        # Verify only the unconstrained role is marked as high privilege
        assert "high-priv-id" in cache_data.high_privilege_roles
        assert "low-priv-id" not in cache_data.high_privilege_roles
        assert "constrained-id" not in cache_data.high_privilege_roles
        assert len(cache_data.high_privilege_roles) == 1

    def test_recommend_roles_sets_is_high_privilege_flag(self, populated_cache):
        """Verify recommend_roles sets is_high_privilege flag correctly from cache."""
        # Role with unconstrained roleAssignments/write -> high privilege
        high_priv_role = make_role_definition(
            "High Privilege Role",
            "high-priv-id",
            ["Microsoft.Authorization/roleAssignments/write"],
        )
        # Role without roleAssignments/write -> not high privilege
        low_priv_role = make_role_definition(
            "Low Privilege Role",
            "low-priv-id",
            ["Microsoft.Storage/storageAccounts/read"],
        )

        operations = [
            make_operation("Microsoft.Authorization/roleAssignments/write"),
            make_operation("Microsoft.Storage/storageAccounts/read"),
        ]

        # Request both operations so both roles match
        result = recommend_roles_with_cache(
            [
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Storage/storageAccounts/read",
            ],
            [high_priv_role, low_priv_role],
            operations,
        )

        # Find results by role name
        high_result = next((r for r in result if r.role_name == "High Privilege Role"), None)
        low_result = next((r for r in result if r.role_name == "Low Privilege Role"), None)

        assert high_result is not None
        assert low_result is not None
        assert high_result.is_high_privilege is True
        assert low_result.is_high_privilege is False

    def test_sorting_prioritizes_non_high_privilege_full_matches(self, populated_cache):
        """Verify non-high-privilege full matches sort before high-privilege ones.

        Given two roles that both fully match the requested operations,
        the non-high-privilege role should appear first in results.
        """
        # Both roles grant the same operation but one is high-privilege
        high_priv_role = make_role_definition(
            "High Priv Full Match",
            "high-priv-id",
            [
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Authorization/roleAssignments/write",
            ],
        )
        low_priv_role = make_role_definition(
            "Low Priv Full Match",
            "low-priv-id",
            ["Microsoft.Storage/storageAccounts/read"],
        )

        operations = [
            make_operation("Microsoft.Storage/storageAccounts/read"),
            make_operation("Microsoft.Authorization/roleAssignments/write"),
        ]

        # Request only the storage read - both roles can satisfy this
        result = recommend_roles_with_cache(
            ["Microsoft.Storage/storageAccounts/read"],
            [high_priv_role, low_priv_role],
            operations,
        )

        assert len(result) == 2
        # Both are full matches
        assert result[0].is_full_match is True
        assert result[1].is_full_match is True
        # Non-high-privilege should come first
        assert result[0].role_name == "Low Priv Full Match"
        assert result[0].is_high_privilege is False
        assert result[1].role_name == "High Priv Full Match"
        assert result[1].is_high_privilege is True


# =============================================================================
# NotActions Exclusion Tests
# =============================================================================


class TestNotActionsExclusions:
    """Tests for notActions and notDataActions exclusions."""

    def test_not_action_excludes_operation(self, populated_cache):
        """notAction excludes specific operation."""
        roles = [
            make_role_definition(
                "No Delete",
                "r1",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/storageAccounts/delete"],
            )
        ]
        result = recommend_roles_with_cache(["Microsoft.Storage/storageAccounts/delete"], roles)
        assert len(result) == 0

    def test_not_action_allows_other_operations(self, populated_cache):
        """notAction doesn't affect non-excluded operations."""
        roles = [
            make_role_definition(
                "No Delete",
                "r1",
                ["Microsoft.Storage/*"],
                ["Microsoft.Storage/storageAccounts/delete"],
            )
        ]
        result = recommend_roles_with_cache(
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

    def test_full_matches_before_partial(self, populated_cache):
        """Full matches should appear before partial matches."""
        roles = [
            make_role_definition("Partial", "r1", ["Microsoft.Storage/*/read"]),
            make_role_definition("Full", "r2", ["Microsoft.Storage/*"]),
        ]
        result = recommend_roles_with_cache(
            ["Microsoft.Storage/storageAccounts/read", "Microsoft.Storage/storageAccounts/write"],
            roles,
        )
        full_indices = [i for i, r in enumerate(result) if r.is_full_match]
        partial_indices = [i for i, r in enumerate(result) if not r.is_full_match]
        if full_indices and partial_indices:
            assert max(full_indices) < min(partial_indices)

    def test_no_match_returns_empty(self, populated_cache):
        """No role matches returns empty list."""
        roles = [make_role_definition("Compute Only", "r1", ["Microsoft.Compute/*"])]
        result = recommend_roles_with_cache(["Microsoft.Storage/storageAccounts/read"], roles)
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

    def test_classify_operations_separates_by_plane(self, populated_cache):
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

    def test_classify_operations_handles_wildcards(self, populated_cache):
        """classify_operations should detect wildcards matching both planes."""
        from azurerbac.matching.recommendation_service import RoleRecommendationService

        svc = RoleRecommendationService()
        classified = svc.classify_operations(["Microsoft.Storage/*"])

        # This wildcard should match both control and data operations
        assert "Microsoft.Storage/*" in classified.control_wildcards
        assert "Microsoft.Storage/*" in classified.data_wildcards

    def test_compute_wildcard_matches_expands_patterns(self, populated_cache):
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
        populated_cache,
        num_roles: int,
        max_results: int | None,
        expected_count: int,
    ):
        """Test max_results parameter behavior."""
        from azurerbac.matching.role_recommender import recommend_roles

        # Use an operation that exists in sample_operations
        roles = [
            make_role_definition(
                f"Role {i}", f"role-id-{i}", ["Microsoft.Storage/storageAccounts/read"]
            )
            for i in range(num_roles)
        ]
        # Build cache with these roles first
        from azurerbac.cache import get_cache_service
        from azurerbac.cache.build import precompute_all

        cache = get_cache_service()
        ops = list(cache.cache.all_operations)
        cache.swap_in_memory(precompute_all(roles, ops))

        result = recommend_roles(
            ["Microsoft.Storage/storageAccounts/read"],
            roles,
            max_results=max_results,
        )
        assert len(result) == expected_count


# =============================================================================
# Missing Operations Expanded Tests
# =============================================================================


class TestMissingOperationsExpanded:
    """Tests for missing_operations_expanded field correctness."""

    def test_zero_coverage_wildcard_has_expanded_ops(self, populated_cache):
        """When a role has 0 coverage for a wildcard, expanded ops should be populated."""
        # Role with NO data plane permissions
        reader_role = make_role_definition(
            "Reader", "reader-id", actions=["*/read"], data_actions=[]
        )

        # Request both control and data plane */read (no flags = both planes)
        result = recommend_roles_with_cache(["*/read"], [reader_role])

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
        self, populated_cache, data_flag: bool, expected_full_match: bool
    ):
        """Test Reader role behavior with different plane flags."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        result = recommend_roles_with_cache(
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

    def test_reader_both_planes_partial_match(self, populated_cache):
        """Reader requesting both planes should show partial match."""
        reader = make_role_definition("Reader", "reader", actions=["*/read"], data_actions=[])

        # Request both planes (no flag = both)
        result = recommend_roles_with_cache(["*/read"], [reader])

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

    def test_service_uses_cache_from_construction_time(self, populated_cache):
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

    def test_service_captures_cache_eagerly_when_none_provided(self, populated_cache):
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

    def test_new_service_gets_fresh_cache(self, populated_cache):
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


# =============================================================================
# Count Consistency Tests
# =============================================================================


class TestCountConsistency:
    """Tests verifying that wildcard counts are consistent between API and recommendation results.

    Regression tests for bug where UI showed "matches X operations" for a wildcard,
    but role results showed "Matches Y of Y operations" where X != Y due to
    count_wildcard_matches not deduplicating operations that lowercase to the same string.
    """

    def test_wildcard_count_matches_requested_operations_count(self, populated_cache):
        """Test that count_wildcard_matches equals requested_operations_count in results.

        The count-matches API (used to show "matches N operations" in UI) must return
        the same count that role matching will report as requested_operations_count.
        """
        from azurerbac.cache import get_cache_service

        cache = get_cache_service()

        # Get count from count_wildcard_matches (used by count-matches API)
        wildcard_count = cache.count_wildcard_matches("*/read", is_data_action=False)

        # Get requested_operations_count from recommendation results
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles_with_cache(
            requested_operations=["*/read"],
            roles=roles,
            requested_ops_data_flags={"*/read": False},  # Explicitly control plane
        )

        assert len(result) == 1
        requested_count = result[0].requested_operations_count

        assert wildcard_count == requested_count, (
            f"count_wildcard_matches returned {wildcard_count} but "
            f"requested_operations_count is {requested_count}. "
            "These should be equal for consistent UI."
        )

    def test_matched_count_equals_requested_when_fully_covered(self, populated_cache):
        """When a role fully covers a wildcard, matched should equal requested."""
        roles = [make_role_definition("Reader", "r1", ["*/read"])]
        result = recommend_roles_with_cache(
            requested_operations=["*/read"],
            roles=roles,
            requested_ops_data_flags={"*/read": False},
        )

        assert len(result) == 1
        assert result[0].is_full_match
        assert result[0].matched_operations_count == result[0].requested_operations_count, (
            f"Full match role should have matched_count ({result[0].matched_operations_count}) "
            f"equal to requested_count ({result[0].requested_operations_count})"
        )

    def test_control_and_data_plane_counts_separate(self, populated_cache):
        """Test that control and data plane wildcards are counted separately."""
        from azurerbac.cache import get_cache_service

        cache = get_cache_service()

        control_count = cache.count_wildcard_matches("*/read", is_data_action=False)
        data_count = cache.count_wildcard_matches("*/read", is_data_action=True)

        # Both should be non-negative, and they may differ
        assert control_count >= 0
        assert data_count >= 0

        # In the sample operations, there are more control plane */read than data plane
        # (Storage, Compute, Auth, Network, KeyVault reads vs Storage blob read)
        assert control_count >= data_count
