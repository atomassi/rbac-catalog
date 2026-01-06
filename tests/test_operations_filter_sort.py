"""Tests for operation filtering and sorting functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from azurerbac.web.services.pages import (
    OperationSearchParams,
    enrich_operations_with_role_counts,
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
) -> dict:
    """Create an operation dict for testing."""
    return {
        "name": name,
        "display_name": display_name,
        "description": description,
        "origin": None,
        "provider_display_name": provider_display_name,
        "resource_type": None,
        "resource_type_display_name": resource_type_display_name,
        "is_data_action": is_data_action,
    }


@pytest.fixture
def sample_operations() -> list[dict]:
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

    def test_matches_name(self, sample_operations: list[dict]):
        """Test matching on operation name."""
        op = sample_operations[0]
        assert operation_matches_search(op, "virtualmachines") is True
        assert operation_matches_search(op, "compute") is True
        assert operation_matches_search(op, "storage") is False

    def test_matches_display_name(self, sample_operations: list[dict]):
        """Test matching on display name."""
        op = sample_operations[0]
        assert operation_matches_search(op, "read virtual") is True
        assert operation_matches_search(op, "delete") is False

    def test_matches_description(self, sample_operations: list[dict]):
        """Test matching on description."""
        op = sample_operations[0]
        assert operation_matches_search(op, "allows reading") is True
        assert operation_matches_search(op, "allows deleting") is False

    def test_matches_provider(self, sample_operations: list[dict]):
        """Test matching on provider display name."""
        op = sample_operations[0]
        assert operation_matches_search(op, "microsoft compute") is True
        assert operation_matches_search(op, "microsoft storage") is False

    def test_matches_resource_type(self, sample_operations: list[dict]):
        """Test matching on resource type display name."""
        op = sample_operations[0]
        assert operation_matches_search(op, "virtual machines") is True
        assert operation_matches_search(op, "blobs") is False

    def test_case_insensitive(self, sample_operations: list[dict]):
        """Test that matching is case insensitive (query must be lowercase)."""
        op = sample_operations[0]
        # Note: query_lower parameter must be lowercase - the caller lowercases it
        assert operation_matches_search(op, "virtualmachines") is True
        assert operation_matches_search(op, "virtual machines") is True
        # The function matches against lowercased operation fields
        assert operation_matches_search(op, "microsoft.compute") is True

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

    def test_no_filters_returns_all(self, sample_operations: list[dict]):
        """Test that no filters returns all operations."""
        params = OperationSearchParams()
        result = filter_operations(sample_operations, params)
        assert len(result) == 4

    def test_query_filter(self, sample_operations: list[dict]):
        """Test filtering by search query."""
        params = OperationSearchParams(query="compute")
        result = filter_operations(sample_operations, params)
        assert len(result) == 1
        assert result[0]["name"] == "Microsoft.Compute/virtualMachines/read"

    def test_query_filter_multiple_matches(self, sample_operations: list[dict]):
        """Test query matching multiple operations."""
        params = OperationSearchParams(query="read")
        result = filter_operations(sample_operations, params)
        assert len(result) == 4  # All have "read" in name or description

    def test_is_data_action_filter_true(self, sample_operations: list[dict]):
        """Test filtering for data actions only."""
        params = OperationSearchParams(is_data_action=True)
        result = filter_operations(sample_operations, params)
        assert len(result) == 2
        assert all(op["is_data_action"] for op in result)

    def test_is_data_action_filter_false(self, sample_operations: list[dict]):
        """Test filtering for control plane actions only."""
        params = OperationSearchParams(is_data_action=False)
        result = filter_operations(sample_operations, params)
        assert len(result) == 2
        assert all(not op["is_data_action"] for op in result)

    def test_provider_filter(self, sample_operations: list[dict]):
        """Test filtering by provider."""
        params = OperationSearchParams(provider="Microsoft Storage")
        result = filter_operations(sample_operations, params)
        assert len(result) == 1
        assert result[0]["provider_display_name"] == "Microsoft Storage"

    def test_combined_filters(self, sample_operations: list[dict]):
        """Test combining multiple filters."""
        params = OperationSearchParams(
            query="read",
            is_data_action=True,
        )
        result = filter_operations(sample_operations, params)
        assert len(result) == 2
        assert all(op["is_data_action"] for op in result)

    def test_no_matches_returns_empty(self, sample_operations: list[dict]):
        """Test that no matches returns empty list."""
        params = OperationSearchParams(query="nonexistent")
        result = filter_operations(sample_operations, params)
        assert len(result) == 0


# =============================================================================
# sort_operations Tests
# =============================================================================


class TestSortOperations:
    """Tests for sort_operations function."""

    def test_sort_by_name_asc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by name ascending."""
        ops = sample_operations.copy()
        sort_operations(ops, "name", "asc", mock_app_cache)
        names = [op["name"] for op in ops]
        assert names == sorted(names, key=str.lower)

    def test_sort_by_name_desc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by name descending."""
        ops = sample_operations.copy()
        sort_operations(ops, "name", "desc", mock_app_cache)
        names = [op["name"] for op in ops]
        assert names == sorted(names, key=str.lower, reverse=True)

    def test_sort_by_provider_asc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by provider ascending."""
        ops = sample_operations.copy()
        sort_operations(ops, "provider", "asc", mock_app_cache)
        providers = [op["provider_display_name"] for op in ops]
        assert providers == sorted(providers, key=str.lower)

    def test_sort_by_type_asc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by type (data action) ascending."""
        ops = sample_operations.copy()
        sort_operations(ops, "type", "asc", mock_app_cache)
        # False comes before True
        types = [op["is_data_action"] for op in ops]
        assert types[:2] == [False, False]
        assert types[2:] == [True, True]

    def test_sort_by_roles_asc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by role count ascending."""
        ops = sample_operations.copy()
        sort_operations(ops, "roles", "asc", mock_app_cache)
        # Check role_count was added
        assert all("role_count" in op for op in ops)
        # Check sorting
        counts = [op["role_count"] for op in ops]
        assert counts == sorted(counts)

    def test_sort_by_roles_desc(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test sorting by role count descending."""
        ops = sample_operations.copy()
        sort_operations(ops, "roles", "desc", mock_app_cache)
        counts = [op["role_count"] for op in ops]
        assert counts == sorted(counts, reverse=True)

    def test_unknown_sort_defaults_to_name(
        self, sample_operations: list[dict], mock_app_cache: MagicMock
    ):
        """Test that unknown sort field defaults to name."""
        ops = sample_operations.copy()
        sort_operations(ops, "unknown", "asc", mock_app_cache)
        names = [op["name"] for op in ops]
        assert names == sorted(names, key=str.lower)


# =============================================================================
# enrich_operations_with_role_counts Tests
# =============================================================================


class TestEnrichOperationsWithRoleCounts:
    """Tests for enrich_operations_with_role_counts function."""

    def test_adds_role_count(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test that role_count is added to operations."""
        ops = sample_operations.copy()
        enrich_operations_with_role_counts(ops, mock_app_cache)
        assert all("role_count" in op for op in ops)

    def test_correct_role_counts(self, sample_operations: list[dict], mock_app_cache: MagicMock):
        """Test that role counts are correct."""
        ops = sample_operations.copy()
        enrich_operations_with_role_counts(ops, mock_app_cache)
        assert ops[0]["role_count"] == 150  # Compute
        assert ops[1]["role_count"] == 50  # Storage
        assert ops[2]["role_count"] == 75  # KeyVault
        assert ops[3]["role_count"] == 200  # Authorization

    def test_skips_existing_role_count(
        self, sample_operations: list[dict], mock_app_cache: MagicMock
    ):
        """Test that existing role_count is not overwritten."""
        ops = sample_operations.copy()
        ops[0]["role_count"] = 999
        enrich_operations_with_role_counts(ops, mock_app_cache)
        assert ops[0]["role_count"] == 999  # Not overwritten
        assert ops[1]["role_count"] == 50  # Added
