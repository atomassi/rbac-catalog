"""Tests for Azure RBAC permission semantics.

Validates that permissions are computed correctly using Azure's per-block semantics:
    effective = union of (block.actions - block.notActions) for each permission block

NOT: (union of all actions) - (union of all notActions)

This matters when one block excludes an action that another block explicitly grants.
"""

import pytest

from azurerbac.cache.build import _compute_role_coverage
from azurerbac.web.services.models import RawPermissions
from tests.helpers import make_role_definition, make_role_with_multiple_permissions

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_control_ops() -> set[str]:
    """Sample control plane operations for testing."""
    return {
        "microsoft.storage/storageaccounts/read",
        "microsoft.storage/storageaccounts/write",
        "microsoft.storage/storageaccounts/delete",
        "microsoft.storage/storageaccounts/listkeys/action",
        "microsoft.compute/virtualmachines/read",
        "microsoft.compute/virtualmachines/write",
        "microsoft.compute/virtualmachines/delete",
        "microsoft.network/virtualnetworks/read",
    }


@pytest.fixture
def sample_data_ops() -> set[str]:
    """Sample data plane operations for testing."""
    return {
        "microsoft.storage/storageaccounts/blobservices/containers/blobs/read",
        "microsoft.storage/storageaccounts/blobservices/containers/blobs/write",
        "microsoft.storage/storageaccounts/blobservices/containers/blobs/delete",
        "microsoft.keyvault/vaults/secrets/read",
        "microsoft.keyvault/vaults/secrets/write",
    }


# =============================================================================
# RawPermissions.compute_effective Tests
# =============================================================================


class TestRawPermissionsComputeEffective:
    """Tests for RawPermissions.compute_effective() with correct semantics."""

    def test_single_block_basic(self, sample_control_ops: set[str], sample_data_ops: set[str]):
        """Single block with actions and notActions."""
        role = make_role_definition(
            role_name="Test",
            role_id="test-1",
            actions=["Microsoft.Storage/*"],
            not_actions=["Microsoft.Storage/storageAccounts/delete"],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # Should include all storage ops except delete
        assert "microsoft.storage/storageaccounts/read" in result.control
        assert "microsoft.storage/storageaccounts/write" in result.control
        assert "microsoft.storage/storageaccounts/delete" not in result.control
        assert "microsoft.compute/virtualmachines/read" not in result.control

    def test_multiple_blocks_exclusion_override(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Critical test: Block 2 grants what Block 1 excludes.

        Block 1: Microsoft.Storage/* EXCEPT write
        Block 2: Microsoft.Storage/storageAccounts/write (explicitly granted)

        Correct result: write SHOULD be granted (Block 2 adds it back)
        Wrong result: write would be excluded (if using global union-then-subtract)
        """
        role = make_role_with_multiple_permissions(
            role_name="Multi-Block Test",
            role_id="multi-1",
            permission_blocks=[
                {
                    "actions": ["Microsoft.Storage/*"],
                    "notActions": ["Microsoft.Storage/storageAccounts/write"],
                },
                {
                    "actions": ["Microsoft.Storage/storageAccounts/write"],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # write MUST be in result - Block 2 grants it without exclusion
        assert "microsoft.storage/storageaccounts/write" in result.control
        # read should also be included (from Block 1)
        assert "microsoft.storage/storageaccounts/read" in result.control

    def test_multiple_blocks_data_plane(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Test per-block semantics for data plane actions."""
        role = make_role_with_multiple_permissions(
            role_name="Data Plane Test",
            role_id="data-1",
            permission_blocks=[
                {
                    "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/*"],
                    "notDataActions": [
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write"
                    ],
                },
                {
                    "dataActions": [
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write"
                    ],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # write MUST be included - Block 2 grants it
        assert (
            "microsoft.storage/storageaccounts/blobservices/containers/blobs/write" in result.data
        )
        assert "microsoft.storage/storageaccounts/blobservices/containers/blobs/read" in result.data

    def test_disjoint_blocks_union(self, sample_control_ops: set[str], sample_data_ops: set[str]):
        """Multiple blocks with non-overlapping permissions should union."""
        role = make_role_with_multiple_permissions(
            role_name="Disjoint Test",
            role_id="disjoint-1",
            permission_blocks=[
                {"actions": ["Microsoft.Storage/storageAccounts/read"]},
                {"actions": ["Microsoft.Compute/virtualMachines/read"]},
                {"actions": ["Microsoft.Network/virtualNetworks/read"]},
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        assert "microsoft.storage/storageaccounts/read" in result.control
        assert "microsoft.compute/virtualmachines/read" in result.control
        assert "microsoft.network/virtualnetworks/read" in result.control
        assert len(result.control) == 3

    def test_empty_blocks_ignored(self, sample_control_ops: set[str], sample_data_ops: set[str]):
        """Empty permission blocks should not affect result."""
        role = make_role_with_multiple_permissions(
            role_name="Empty Block Test",
            role_id="empty-1",
            permission_blocks=[
                {"actions": ["Microsoft.Storage/storageAccounts/read"]},
                {"actions": []},  # Empty block
                {"notActions": ["Microsoft.Compute/*"]},  # notActions without actions
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        assert "microsoft.storage/storageaccounts/read" in result.control
        assert len(result.control) == 1

    def test_overlapping_grants_idempotent(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Multiple blocks granting same action should result in single grant."""
        role = make_role_with_multiple_permissions(
            role_name="Overlap Test",
            role_id="overlap-1",
            permission_blocks=[
                {"actions": ["Microsoft.Storage/storageAccounts/read"]},
                {"actions": ["Microsoft.Storage/storageAccounts/read"]},
                {"actions": ["Microsoft.Storage/*"]},
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # read should appear once in result
        assert "microsoft.storage/storageaccounts/read" in result.control


# =============================================================================
# Cache Build _compute_role_coverage Tests
# =============================================================================


class TestCacheBuildRoleCoverage:
    """Tests for _compute_role_coverage with correct per-block semantics."""

    def test_multiple_blocks_exclusion_override(self, sample_control_ops: set[str]):
        """Cache build must use per-block semantics."""
        role = make_role_with_multiple_permissions(
            role_name="Cache Test",
            role_id="cache-1",
            permission_blocks=[
                {
                    "actions": ["Microsoft.Storage/*"],
                    "notActions": ["Microsoft.Storage/storageAccounts/write"],
                },
                {
                    "actions": ["Microsoft.Storage/storageAccounts/write"],
                },
            ],
        )

        result = _compute_role_coverage(
            role=role,
            all_control_ops=sample_control_ops,
            all_data_ops=set(),
            pattern_match={},
            control_ops_lower_to_orig={op: op for op in sample_control_ops},
            data_ops_lower_to_orig={},
        )

        # write MUST be included - Block 2 grants it
        assert "microsoft.storage/storageaccounts/write" in result.control
        assert "microsoft.storage/storageaccounts/read" in result.control

    def test_cache_data_plane_per_block(self, sample_data_ops: set[str]):
        """Cache build uses per-block semantics for data plane."""
        role = make_role_with_multiple_permissions(
            role_name="Data Cache Test",
            role_id="data-cache-1",
            permission_blocks=[
                {
                    "dataActions": ["Microsoft.KeyVault/vaults/secrets/*"],
                    "notDataActions": ["Microsoft.KeyVault/vaults/secrets/write"],
                },
                {
                    "dataActions": ["Microsoft.KeyVault/vaults/secrets/write"],
                },
            ],
        )

        result = _compute_role_coverage(
            role=role,
            all_control_ops=set(),
            all_data_ops=sample_data_ops,
            pattern_match={},
            control_ops_lower_to_orig={},
            data_ops_lower_to_orig={op: op for op in sample_data_ops},
        )

        # write MUST be included
        assert "microsoft.keyvault/vaults/secrets/write" in result.data
        assert "microsoft.keyvault/vaults/secrets/read" in result.data


# =============================================================================
# Edge Cases and Complex Scenarios
# =============================================================================


class TestComplexPermissionScenarios:
    """Complex permission scenarios testing edge cases."""

    def test_three_blocks_cascading_overrides(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Three blocks with cascading grants and exclusions.

        Block 1: All storage except write and delete
        Block 2: write (granted back)
        Block 3: delete (granted back)

        Result: All storage operations should be granted
        """
        role = make_role_with_multiple_permissions(
            role_name="Cascade Test",
            role_id="cascade-1",
            permission_blocks=[
                {
                    "actions": ["Microsoft.Storage/*"],
                    "notActions": [
                        "Microsoft.Storage/storageAccounts/write",
                        "Microsoft.Storage/storageAccounts/delete",
                    ],
                },
                {"actions": ["Microsoft.Storage/storageAccounts/write"]},
                {"actions": ["Microsoft.Storage/storageAccounts/delete"]},
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # All storage ops should be included
        assert "microsoft.storage/storageaccounts/read" in result.control
        assert "microsoft.storage/storageaccounts/write" in result.control
        assert "microsoft.storage/storageaccounts/delete" in result.control
        assert "microsoft.storage/storageaccounts/listkeys/action" in result.control

    def test_mixed_control_and_data_planes(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Single block with both control and data plane actions."""
        role = make_role_with_multiple_permissions(
            role_name="Mixed Planes",
            role_id="mixed-1",
            permission_blocks=[
                {
                    "actions": ["Microsoft.Storage/*"],
                    "notActions": ["Microsoft.Storage/storageAccounts/delete"],
                    "dataActions": ["Microsoft.Storage/storageAccounts/blobServices/*"],
                    "notDataActions": [
                        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/delete"
                    ],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # Control plane
        assert "microsoft.storage/storageaccounts/read" in result.control
        assert "microsoft.storage/storageaccounts/delete" not in result.control

        # Data plane
        assert "microsoft.storage/storageaccounts/blobservices/containers/blobs/read" in result.data
        assert (
            "microsoft.storage/storageaccounts/blobservices/containers/blobs/delete"
            not in result.data
        )

    def test_block_with_only_exclusions_is_noop(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """A block with only notActions and no actions grants nothing."""
        role = make_role_with_multiple_permissions(
            role_name="Exclusion Only",
            role_id="excl-only-1",
            permission_blocks=[
                {"actions": ["Microsoft.Storage/storageAccounts/read"]},
                {
                    # This block grants nothing - notActions without actions
                    "notActions": ["Microsoft.Storage/*"],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # Only read should be granted (from Block 1)
        assert "microsoft.storage/storageaccounts/read" in result.control
        assert len(result.control) == 1


# =============================================================================
# Regression Tests
# =============================================================================


class TestPermissionSemanticsRegression:
    """Regression tests to ensure we don't revert to incorrect semantics."""

    def test_wrong_global_subtraction_would_fail(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Document the bug: global subtraction gives wrong answer.

        This test documents what the WRONG behavior would produce,
        ensuring we catch regressions.
        """
        role = make_role_with_multiple_permissions(
            role_name="Regression Test",
            role_id="regression-1",
            permission_blocks=[
                {
                    "actions": ["Microsoft.Storage/*"],
                    "notActions": ["Microsoft.Storage/storageAccounts/write"],
                },
                {
                    "actions": ["Microsoft.Storage/storageAccounts/write"],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # CORRECT: write IS granted (Block 2 explicitly allows it)
        assert "microsoft.storage/storageaccounts/write" in result.control

        # If we were using wrong semantics (global union then subtract),
        # write would NOT be in result. This test catches that regression.

    def test_real_world_storage_role_pattern(
        self, sample_control_ops: set[str], sample_data_ops: set[str]
    ):
        """Test pattern similar to real Azure built-in roles.

        Some Azure roles grant broad access then add back specific excluded ops.
        """
        role = make_role_with_multiple_permissions(
            role_name="Storage Special",
            role_id="storage-special-1",
            permission_blocks=[
                {
                    # Broad management access
                    "actions": ["Microsoft.Storage/storageAccounts/*"],
                    "notActions": [
                        "Microsoft.Storage/storageAccounts/delete",
                        "Microsoft.Storage/storageAccounts/listkeys/action",
                    ],
                },
                {
                    # But listkeys IS needed for this role
                    "actions": ["Microsoft.Storage/storageAccounts/listkeys/action"],
                },
            ],
        )
        raw = RawPermissions.from_permissions(role.properties.permissions)

        result = raw.compute_effective(sample_control_ops, sample_data_ops)

        # listkeys should be granted (Block 2)
        assert "microsoft.storage/storageaccounts/listkeys/action" in result.control
        # delete should NOT be granted (excluded, no override)
        assert "microsoft.storage/storageaccounts/delete" not in result.control
        # read/write should be granted (Block 1)
        assert "microsoft.storage/storageaccounts/read" in result.control
        assert "microsoft.storage/storageaccounts/write" in result.control
