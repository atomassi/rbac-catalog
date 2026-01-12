"""Tests for API Pydantic models."""

import pytest

from azurerbac.web.routes.models import OperationItem, RecommendRolesRequest


class TestRecommendRolesRequest:
    """Tests for RecommendRolesRequest.parse_operations() method."""

    @pytest.mark.parametrize(
        ("ops", "expected_names", "expected_flags"),
        [
            pytest.param(
                [("Microsoft.Storage/read", False)],
                ["Microsoft.Storage/read"],
                {"Microsoft.Storage/read": False},
                id="single_operation",
            ),
            pytest.param(
                [("op3", False), ("op1", True), ("op2", False)],
                ["op3", "op1", "op2"],
                {"op3": False, "op1": True, "op2": False},
                id="preserves_order",
            ),
            pytest.param(
                [("control", False), ("data", True), ("also_control", False)],
                ["control", "data", "also_control"],
                {"control": False, "data": True, "also_control": False},
                id="mixed_data_planes",
            ),
            pytest.param(
                [("Microsoft.Storage/blobs/read", True)],
                ["Microsoft.Storage/blobs/read"],
                {"Microsoft.Storage/blobs/read": True},
                id="data_action_true",
            ),
        ],
    )
    def test_parse_operations(self, ops, expected_names, expected_flags):
        """Test parsing operations with various inputs."""
        request = RecommendRolesRequest(
            operations=[OperationItem(name=n, is_data_action=d) for n, d in ops]
        )
        names, flags = request.parse_operations()

        assert names == expected_names
        assert flags == expected_flags

    def test_deduplicates_operations(self):
        """Duplicate operations are deduplicated, first wins for order."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="op1", is_data_action=False),
                OperationItem(name="op2", is_data_action=True),
                OperationItem(name="op1", is_data_action=False),
            ]
        )
        names, _ = request.parse_operations()

        assert names == ["op1", "op2"]

    @pytest.mark.parametrize(
        ("ops", "expected_names", "flags_is_none"),
        [
            pytest.param(
                [("*/read", False), ("*/read", True)],
                ["*/read"],
                True,
                id="conflicting_data_planes",
            ),
            pytest.param(
                [("op", False), ("op", True)],
                ["op"],
                True,
                id="all_conflicting_returns_none",
            ),
        ],
    )
    def test_conflicting_data_planes(self, ops, expected_names, flags_is_none):
        """Same operation with both data planes omits flag for auto-detection."""
        request = RecommendRolesRequest(
            operations=[OperationItem(name=n, is_data_action=d) for n, d in ops]
        )
        names, flags = request.parse_operations()

        assert names == expected_names
        if flags_is_none:
            assert flags is None or expected_names[0] not in flags

    def test_empty_name_skipped(self):
        """Operations with empty names are skipped."""
        request = RecommendRolesRequest(
            operations=[
                OperationItem(name="valid", is_data_action=False),
                OperationItem(name="", is_data_action=False),
            ]
        )
        names, flags = request.parse_operations()

        assert names == ["valid"]
        assert "valid" in flags
