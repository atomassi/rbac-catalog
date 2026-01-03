"""Tests for API Pydantic models."""

from azurerbac.web.routes.models import OperationItem, RecommendRolesRequest


class TestRecommendRolesRequest:
    """Tests for RecommendRolesRequest.parse_operations() method."""

    def test_parse_single_operation(self):
        """Single operation returns name and flag."""
        request = RecommendRolesRequest(
            operations=[OperationItem(name="Microsoft.Storage/read", is_data_action=False)]
        )
        names, flags = request.parse_operations()

        assert names == ["Microsoft.Storage/read"]
        assert flags == {"Microsoft.Storage/read": False}

    def test_parse_multiple_operations_preserves_order(self):
        """Multiple operations preserve insertion order."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="op3", is_data_action=False),
                OperationItem(name="op1", is_data_action=True),
                OperationItem(name="op2", is_data_action=False),
            ]
        )
        names, flags = request.parse_operations()

        assert names == ["op3", "op1", "op2"]
        assert flags == {"op3": False, "op1": True, "op2": False}

    def test_parse_deduplicates_operations(self):
        """Duplicate operations are deduplicated, first wins for order."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="op1", is_data_action=False),
                OperationItem(name="op2", is_data_action=True),
                OperationItem(name="op1", is_data_action=False),  # Duplicate
            ]
        )
        names, _flags = request.parse_operations()

        assert names == ["op1", "op2"]
        assert len(names) == 2

    def test_parse_conflicting_data_planes_omits_flag(self):
        """Same operation with both data planes omits flag for auto-detection."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="*/read", is_data_action=False),  # Control plane
                OperationItem(name="*/read", is_data_action=True),  # Data plane
            ]
        )
        names, flags = request.parse_operations()

        assert names == ["*/read"]
        assert flags is None or "*/read" not in flags

    def test_parse_empty_name_skipped(self):
        """Operations with empty names are skipped."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="valid", is_data_action=False),
                OperationItem(name="", is_data_action=False),  # Empty - skipped
            ]
        )
        names, flags = request.parse_operations()

        assert names == ["valid"]
        assert "valid" in flags

    def test_parse_returns_none_for_empty_flags(self):
        """Returns None for flags when all operations conflict."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="op", is_data_action=False),
                OperationItem(name="op", is_data_action=True),  # Conflict
            ]
        )
        names, flags = request.parse_operations()

        assert names == ["op"]
        assert flags is None

    def test_parse_data_action_true(self):
        """Data plane operations have is_data_action=True."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(
                    name="Microsoft.Storage/blobServices/containers/blobs/read", is_data_action=True
                )
            ]
        )
        _names, flags = request.parse_operations()

        assert flags["Microsoft.Storage/blobServices/containers/blobs/read"] is True

    def test_parse_mixed_data_planes(self):
        """Mix of data and control plane operations preserved correctly."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="control", is_data_action=False),
                OperationItem(name="data", is_data_action=True),
                OperationItem(name="also_control", is_data_action=False),
            ]
        )
        _names, flags = request.parse_operations()

        assert flags["control"] is False
        assert flags["data"] is True
        assert flags["also_control"] is False
