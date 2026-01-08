"""Tests for operation filtering and sorting functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azurerbac.azure.models import OperationData
from azurerbac.web.services.pages import (
    OperationSearchParams,
    filter_operations,
    operation_matches_search,
    sort_operations,
)

# =============================================================================
# Test Fixtures
# =============================================================================


def make_operation(
    name: str,
    display_name: str | None = None,
    description: str | None = None,
    provider_display_name: str = "",
    resource_type_display_name: str | None = None,
    is_data_action: bool = False,
) -> OperationData:
    """Create an OperationData object for testing."""
    return OperationData(
        name=name,
        display_name=display_name,
        description=description,
        origin=None,
        provider_display_name=provider_display_name,
        resource_type=None,
        resource_type_display_name=resource_type_display_name,
        is_data_action=is_data_action,
    )


@pytest.fixture
def sample_operations() -> list[OperationData]:
    """Sample operations for testing."""
    return [
        make_operation(
            name="Microsoft.Compute/virtualMachines/read",
            display_name="Read Virtual Machines",
            description="Allows reading virtual machines",
            provider_display_name="Microsoft Compute",
            resource_type_display_name="Virtual Machines",
            is_data_action=False,
        ),
        make_operation(
            name="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
            display_name="Read Blobs",
            description="Read blob data",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Blobs",
            is_data_action=True,
        ),
        make_operation(
            name="Microsoft.KeyVault/vaults/secrets/read",
            display_name="Read Secrets",
            description="Read secrets from key vault",
            provider_display_name="Microsoft Key Vault",
            resource_type_display_name="Secrets",
            is_data_action=True,
        ),
        make_operation(
            name="Microsoft.Authorization/roleDefinitions/read",
            display_name="Read Role Definitions",
            description="Read RBAC role definitions",
            provider_display_name="Microsoft Authorization",
            resource_type_display_name="Role Definitions",
            is_data_action=False,
        ),
    ]


@pytest.fixture
def mock_app_cache() -> MagicMock:
    """Create a mock app cache."""
    cache = MagicMock()
    # Mock role counts: more roles for common operations
    cache.get_operation_role_count.side_effect = lambda name: {
        "Microsoft.Compute/virtualMachines/read": 150,
        "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read": 50,
        "Microsoft.KeyVault/vaults/secrets/read": 75,
        "Microsoft.Authorization/roleDefinitions/read": 200,
    }.get(name, 0)
    return cache


# =============================================================================
# operation_matches_search Tests
# =============================================================================


class TestOperationMatchesSearch:
    """Tests for operation_matches_search function."""

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            pytest.param("virtualmachines", True, id="name_match"),
            pytest.param("compute", True, id="name_provider"),
            pytest.param("read virtual", True, id="display_name_match"),
            pytest.param("allows reading", True, id="description_match"),
            pytest.param("microsoft compute", True, id="provider_match"),
            pytest.param("virtual machines", True, id="resource_type_match"),
            pytest.param("microsoft.compute", True, id="case_insensitive"),
            pytest.param("storage", False, id="no_match_storage"),
            pytest.param("delete", False, id="no_match_delete"),
            pytest.param("blobs", False, id="no_match_blobs"),
        ],
    )
    def test_operation_matches_search(
        self, sample_operations: list[OperationData], query: str, expected: bool
    ):
        """Test operation matching against various search queries."""
        op = sample_operations[0]  # Microsoft.Compute/virtualMachines/read
        assert operation_matches_search(op, query) is expected

    def test_handles_none_fields(self):
        """Test that None fields don't cause errors."""
        op = make_operation(name="test/operation")
        assert operation_matches_search(op, "test") is True
        assert operation_matches_search(op, "display") is False


# =============================================================================
# filter_operations Tests
# =============================================================================


class TestFilterOperations:
    """Tests for filter_operations function."""

    def test_no_filters_returns_all(self, sample_operations: list[OperationData]):
        """Test that no filters returns all operations."""
        params = OperationSearchParams()
        result = filter_operations(sample_operations, params)
        assert len(result) == 4

    def test_query_filter(self, sample_operations: list[OperationData]):
        """Test filtering by search query."""
        params = OperationSearchParams(query="compute")
        result = filter_operations(sample_operations, params)
        assert len(result) == 1
        assert result[0].name == "Microsoft.Compute/virtualMachines/read"

    def test_query_filter_multiple_matches(self, sample_operations: list[OperationData]):
        """Test query matching multiple operations."""
        params = OperationSearchParams(query="read")
        result = filter_operations(sample_operations, params)
        assert len(result) == 4  # All have "read" in name or description

    @pytest.mark.parametrize(
        ("is_data_action", "expected_count"),
        [
            pytest.param(True, 2, id="data_actions_only"),
            pytest.param(False, 2, id="control_plane_only"),
        ],
    )
    def test_is_data_action_filter(
        self, sample_operations: list[OperationData], is_data_action: bool, expected_count: int
    ):
        """Test filtering by data action type."""
        params = OperationSearchParams(is_data_action=is_data_action)
        result = filter_operations(sample_operations, params)
        assert len(result) == expected_count
        assert all(op.is_data_action == is_data_action for op in result)

    def test_provider_filter(self, sample_operations: list[OperationData]):
        """Test filtering by provider."""
        params = OperationSearchParams(provider="Microsoft Storage")
        result = filter_operations(sample_operations, params)
        assert len(result) == 1
        assert result[0].provider_display_name == "Microsoft Storage"

    def test_combined_filters(self, sample_operations: list[OperationData]):
        """Test combining multiple filters."""
        params = OperationSearchParams(
            query="read",
            is_data_action=True,
        )
        result = filter_operations(sample_operations, params)
        assert len(result) == 2
        assert all(op.is_data_action for op in result)

    def test_no_matches_returns_empty(self, sample_operations: list[OperationData]):
        """Test that no matches returns empty list."""
        params = OperationSearchParams(query="nonexistent")
        result = filter_operations(sample_operations, params)
        assert len(result) == 0


# =============================================================================
# sort_operations Tests
# =============================================================================


class TestSortOperations:
    """Tests for sort_operations function."""

    @pytest.mark.parametrize(
        ("sort_field", "order"),
        [
            pytest.param("name", "asc", id="name_asc"),
            pytest.param("name", "desc", id="name_desc"),
            pytest.param("provider", "asc", id="provider_asc"),
        ],
    )
    def test_sort_by_field(
        self,
        sample_operations: list[OperationData],
        mock_app_cache: MagicMock,
        sort_field: str,
        order: str,
    ):
        """Test sorting by various fields."""
        result = sort_operations(sample_operations, sort_field, order, mock_app_cache)
        # Result is list of (OperationData, role_count) tuples
        if sort_field == "name":
            values = [op.name for op, _ in result]
            expected = sorted(values, key=str.lower, reverse=(order == "desc"))
        else:  # provider
            values = [op.provider_display_name or "" for op, _ in result]
            expected = sorted(values, key=str.lower, reverse=(order == "desc"))
        assert values == expected

    def test_sort_by_type_asc(
        self, sample_operations: list[OperationData], mock_app_cache: MagicMock
    ):
        """Test sorting by type (data action) ascending."""
        result = sort_operations(sample_operations, "type", "asc", mock_app_cache)
        # False comes before True
        types = [op.is_data_action for op, _ in result]
        assert types[:2] == [False, False]
        assert types[2:] == [True, True]

    @pytest.mark.parametrize(
        "order",
        [
            pytest.param("asc", id="roles_asc"),
            pytest.param("desc", id="roles_desc"),
        ],
    )
    def test_sort_by_roles(
        self, sample_operations: list[OperationData], mock_app_cache: MagicMock, order: str
    ):
        """Test sorting by role count."""
        result = sort_operations(sample_operations, "roles", order, mock_app_cache)
        # Check sorting by role_count (second element of tuple)
        counts = [role_count for _, role_count in result]
        assert counts == sorted(counts, reverse=(order == "desc"))

    def test_unknown_sort_defaults_to_name(
        self, sample_operations: list[OperationData], mock_app_cache: MagicMock
    ):
        """Test that unknown sort field defaults to name."""
        result = sort_operations(sample_operations, "unknown", "asc", mock_app_cache)
        names = [op.name for op, _ in result]
        assert names == sorted(names, key=str.lower)
