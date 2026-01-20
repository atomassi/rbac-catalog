"""Tests for the page-related service logic.

Keep these tests focused on meaningful behavior (permissions computation, role lookup,
provider extraction). Route wiring and template rendering are covered by E2E.
"""

from unittest.mock import MagicMock

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedChangeEvent
from azurerbac.core.constants import EventType
from tests.helpers import make_role_definition

# =============================================================================
# Tests for pages service functions
# These tests cover the business logic extracted from the pages routes
# into the services module.
# =============================================================================


class TestComputeRoleEffectivePermissionsServices:
    """Tests for compute_role_effective_permissions function in services module."""

    def test_compute_from_cache(self):
        """Test computing effective permissions from cache."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role", "test-role-id", actions=["Microsoft.Storage/*/read"]
        )

        all_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
        ]

        # Create mock app_cache with pre-computed coverage
        mock_app_cache = MagicMock()
        # Cache stores lowered operation names
        mock_app_cache.get_role_coverage.return_value = (
            {"microsoft.storage/storageaccounts/read"},
            set(),
        )
        # Mock restore_operation_casing to return original casing
        ops_map = {
            "microsoft.storage/storageaccounts/read": "Microsoft.Storage/storageAccounts/read",
            "microsoft.storage/storageaccounts/write": "Microsoft.Storage/storageAccounts/write",
        }
        mock_app_cache.restore_operation_casing.side_effect = lambda ops: [
            ops_map.get(op, op) for op in ops
        ]

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result.control_plane_count == 1
        assert result.data_plane_count == 0
        assert "Microsoft.Storage/storageAccounts/read" in result.control_plane_actions

    def test_compute_with_not_actions(self):
        """Test that notActions are properly excluded."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role",
            "test-role-id",
            actions=["Microsoft.Storage/*"],
            not_actions=["Microsoft.Storage/storageAccounts/delete"],
        )

        all_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/delete", "is_data_action": False},
        ]

        # Create mock with delete excluded (cache stores lowered)
        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            {
                "microsoft.storage/storageaccounts/read",
                "microsoft.storage/storageaccounts/write",
            },
            set(),
        )
        ops_map = {
            "microsoft.storage/storageaccounts/read": "Microsoft.Storage/storageAccounts/read",
            "microsoft.storage/storageaccounts/write": "Microsoft.Storage/storageAccounts/write",
            "microsoft.storage/storageaccounts/delete": "Microsoft.Storage/storageAccounts/delete",
        }
        mock_app_cache.restore_operation_casing.side_effect = lambda ops: [
            ops_map.get(op, op) for op in ops
        ]

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result.control_plane_count == 2
        assert "Microsoft.Storage/storageAccounts/delete" not in result.control_plane_actions

    def test_compute_data_actions(self):
        """Test computing data plane actions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role",
            "test-role-id",
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
        )

        all_operations = [
            {
                "name": "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
                "is_data_action": True,
            },
        ]

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            set(),
            {"microsoft.storage/storageaccounts/blobservices/containers/blobs/read"},
        )
        blob_op = "microsoft.storage/storageaccounts/blobservices/containers/blobs/read"
        ops_map = {
            blob_op: "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
        }
        mock_app_cache.restore_operation_casing.side_effect = lambda ops: [
            ops_map.get(op, op) for op in ops
        ]

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result.control_plane_count == 0
        assert result.data_plane_count == 1

    @pytest.mark.parametrize(
        ("actions", "condition", "cache_coverage", "attr", "expected"),
        [
            pytest.param(
                ["Microsoft.Storage/*/read"],
                "@Resource[Microsoft.Storage/storageAccounts:name] == 'example'",
                (set(), set()),
                "has_conditions",
                True,
                id="detect_conditions",
            ),
            pytest.param(
                ["Microsoft.Storage/*/read"],
                None,
                (set(), set()),
                "has_wildcards",
                True,
                id="detect_wildcards",
            ),
            pytest.param(
                ["Microsoft.OldService/*/read"],
                None,
                (set(), set()),
                "has_unresolved_permissions",
                True,
                id="unresolved_permissions",
            ),
            pytest.param(
                ["Microsoft.Storage/*/read"],
                None,
                ({"microsoft.storage/storageaccounts/read"}, set()),
                "has_unresolved_permissions",
                False,
                id="resolved_permissions",
            ),
        ],
    )
    def test_permission_detection(
        self,
        actions: list[str],
        condition: str | None,
        cache_coverage: tuple[set, set],
        attr: str,
        expected: bool,
    ):
        """Test detection of various permission attributes (conditions, wildcards, unresolved)."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role", "test-role-id", actions=actions, condition=condition
        )

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = cache_coverage
        ops_map = {
            "microsoft.storage/storageaccounts/read": "Microsoft.Storage/storageAccounts/read",
        }
        mock_app_cache.restore_operation_casing.side_effect = lambda ops: [
            ops_map.get(op, op) for op in ops
        ]

        result = compute_role_effective_permissions(role, [], mock_app_cache)

        assert getattr(result, attr) == expected

    def test_fallback_to_manual_computation(self):
        """Test fallback to manual computation when cache misses."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role",
            "test-role-id",
            actions=["Microsoft.Storage/storageAccounts/read"],
        )

        all_operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/write", is_data_action=False),
        ]

        mock_app_cache = MagicMock()
        # Cache miss
        mock_app_cache.get_role_coverage.return_value = None
        ops_map = {
            "microsoft.storage/storageaccounts/read": "Microsoft.Storage/storageAccounts/read",
            "microsoft.storage/storageaccounts/write": "Microsoft.Storage/storageAccounts/write",
        }
        mock_app_cache.restore_operation_casing.side_effect = lambda ops: [
            ops_map.get(op, op) for op in ops
        ]

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        # Should compute manually
        assert result.control_plane_count == 1
        assert "Microsoft.Storage/storageAccounts/read" in result.control_plane_actions


class TestGetRolesAllowingOperationServices:
    """Tests for get_roles_allowing_operation function in services module."""

    def test_returns_roles_from_cache(self):
        """Test returning cached results."""
        from azurerbac.web.services.models import RoleAllowingOperation
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        cached_roles = [
            RoleAllowingOperation(
                role_id="role1",
                role_name="Storage Reader",
                role_type="BuiltInRole",
                matched_pattern="Microsoft.Storage/read",
                actions_count=1,
                data_actions_count=0,
                has_condition=False,
                condition_text=None,
            )
        ]
        mock_app_cache.get.return_value = cached_roles

        result = get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)

        assert result == cached_roles
        mock_app_cache.get.assert_called_once()

    def test_finds_roles_by_operation(self):
        """Test finding roles that allow an operation."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get.return_value = None  # Cache miss

        # Mock role data using RoleDefinition
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "role1",
                    "properties": {
                        "roleName": "Storage Reader",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["Microsoft.Storage/storageAccounts/read"]}],
                    },
                }
            )
        ]
        mock_app_cache.get_all_roles.return_value = roles
        # Cache stores lowered operation names for O(1) lookup
        mock_app_cache.get_role_coverage.return_value = (
            {"microsoft.storage/storageaccounts/read"},
            set(),
        )

        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read", False, mock_app_cache
        )

        assert len(result) == 1
        assert result[0].role_name == "Storage Reader"
        assert result[0].role_id == "role1"

    def test_excludes_roles_without_operation(self):
        """Test that roles without the operation are excluded."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get.return_value = None

        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "role1",
                    "properties": {
                        "roleName": "Compute Reader",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["Microsoft.Compute/*/read"]}],
                    },
                }
            )
        ]
        mock_app_cache.get_all_roles.return_value = roles
        # Role doesn't cover Storage operations
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Compute/virtualMachines/read"},
            set(),
        )

        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read", False, mock_app_cache
        )

        assert len(result) == 0

    def test_caches_result(self):
        """Test that results are cached."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get.return_value = None

        # Need at least one role for caching to happen
        roles = [
            RoleDefinition.model_validate(
                {
                    "name": "role1",
                    "properties": {
                        "roleName": "Test Role",
                        "type": "BuiltInRole",
                        "permissions": [{"actions": ["*"]}],
                    },
                }
            )
        ]
        mock_app_cache.get_all_roles.return_value = roles
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Storage/read"},
            set(),
        )

        get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)

        # Should cache the result
        mock_app_cache.set.assert_called_once()
        cache_key = mock_app_cache.set.call_args[0][0]
        assert "roles_allowing_op:" in cache_key


class TestEnrichEventWithDiff:
    """Tests for enrich_event_with_diff function."""

    def test_enriches_event_with_role_json_pretty(self):
        """Test that role_json is formatted as role_json_pretty."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=1,
            role_id="test-id",
            role_name="Test Role",
            event_type=EventType.CREATED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Role created",
            diff_json={"changed": True, "changes": []},
            role_json={
                "id": "test-id",
                "name": "test-guid",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {"roleName": "Test Role", "type": "BuiltInRole"},
            },
        )

        result = enrich_event_with_diff(event)

        assert result.role_json_pretty != ""
        assert '"roleName": "Test Role"' in result.role_json_pretty
        assert '"type": "BuiltInRole"' in result.role_json_pretty

    def test_role_json_pretty_empty_when_no_role_json(self):
        """Test that role_json_pretty is empty for deleted events."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=2,
            role_id="test-id",
            role_name="Deleted Role",
            event_type=EventType.DELETED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Role deleted",
            diff_json={"changed": True, "changes": []},
            role_json=None,  # NULL for deleted
        )

        result = enrich_event_with_diff(event)

        assert result.role_json_pretty == ""

    def test_applies_sanitization_to_role_json(self):
        """Test that role_json is sanitized (isServiceRole excluded via RoleDefinition model)."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=3,
            role_id="test",
            role_name="Test",
            event_type=EventType.CREATED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Created",
            diff_json={},
            role_json={
                "id": "test",
                "name": "test-guid",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {"roleName": "Test", "isServiceRole": True},
            },
        )

        result = enrich_event_with_diff(event)

        # isServiceRole is excluded by RoleDefinition.to_dict()
        assert "isServiceRole" not in result.role_json_pretty
        assert "roleName" in result.role_json_pretty

    def test_diff_json_is_processed(self):
        """Test that diff_json is included in result."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=4,
            role_id="test-id",
            role_name="Updated Role",
            event_type=EventType.UPDATED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Updated",
            diff_json={
                "changed": True,
                "changes": [{"path": "description", "from": "old", "to": "new"}],
            },
            role_json=None,
        )

        result = enrich_event_with_diff(event)

        assert result.diff is not None
        assert result.diff["changed"] is True
        assert len(result.diff["changes"]) == 1
