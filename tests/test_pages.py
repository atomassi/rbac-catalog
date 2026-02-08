"""Tests for the page-related service logic.

Keep these tests focused on meaningful behavior (permissions computation, role lookup,
provider extraction). Route wiring and template rendering are covered by E2E.
"""

from unittest.mock import MagicMock

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedChangeEvent, CachedRole
from azurerbac.core.constants import ROLE_DEFINITION_TYPE, EventType, RoleStatus
from azurerbac.matching.models import RoleCoverage
from tests.helpers import make_cached_role, make_role_definition

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

    def test_cache_miss_lowercases_operations_for_restore(self):
        """Test that cache miss path lowercases operations for restore_operation_casing.

        This is a regression test for a bug where compute_coverage passed operations
        with original casing, but restore_operation_casing expected lowercased names.
        """
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role_definition(
            "Test Role",
            "test-role-id",
            actions=["Microsoft.Storage/storageAccounts/read"],
        )

        # Operations with mixed casing (as returned by Azure API)
        all_operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Compute/virtualMachines/read", is_data_action=False),
        ]

        mock_app_cache = MagicMock()
        # Cache miss - forces manual computation
        mock_app_cache.get_role_coverage.return_value = None

        # Track what restore_operation_casing receives
        received_ops: list[list[str]] = []

        def track_restore(ops):
            received_ops.append(list(ops))
            # Simulate the mapping
            ops_map = {
                "microsoft.storage/storageaccounts/read": "Microsoft.Storage/storageAccounts/read",
            }
            return [ops_map.get(op, op) for op in ops]

        mock_app_cache.restore_operation_casing.side_effect = track_restore

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        # Verify restore_operation_casing was called with LOWERCASED operations
        assert len(received_ops) == 2  # control + data
        control_ops_received = received_ops[0]
        assert "microsoft.storage/storageaccounts/read" in control_ops_received
        # Should NOT contain original casing
        assert "Microsoft.Storage/storageAccounts/read" not in control_ops_received

        # Final result should have restored casing
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
        mock_app_cache.get_allowing_roles.return_value = cached_roles

        result = get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)

        assert result == cached_roles
        mock_app_cache.get_allowing_roles.assert_called_once()

    def test_finds_roles_by_operation(self):
        """Test finding roles that allow an operation via inverted index."""
        from azurerbac.cache.models import CachedRole
        from azurerbac.core.constants import RoleStatus
        from azurerbac.matching.models import RoleNetPermissions
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get_allowing_roles.return_value = None  # Cache miss

        # Mock role data using RoleDefinition
        role = RoleDefinition.model_validate(
            {
                "name": "role1",
                "properties": {
                    "roleName": "Storage Reader",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["Microsoft.Storage/storageAccounts/read"]}],
                },
            }
        )

        # Mock the inverted index lookup
        mock_app_cache.get_roles_for_operation.return_value = ["role1"]
        mock_app_cache.get_role_by_id.return_value = CachedRole(
            definition=role,
            status=RoleStatus.ACTIVE,
        )
        mock_app_cache.get_role_coverage.return_value = (
            {"microsoft.storage/storageaccounts/read"},
            set(),
        )
        mock_app_cache.get_role_net_permissions.return_value = RoleNetPermissions(1, 0)

        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read", False, mock_app_cache
        )

        assert len(result) == 1
        assert result[0].role_name == "Storage Reader"
        assert result[0].role_id == "role1"

    def test_excludes_roles_without_operation(self):
        """Test that roles without the operation are excluded (empty index result)."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get_allowing_roles.return_value = None

        # Inverted index returns no roles for this operation
        mock_app_cache.get_roles_for_operation.return_value = []

        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read", False, mock_app_cache
        )

        assert len(result) == 0

    def test_caches_result(self):
        """Test that results are cached."""
        from azurerbac.cache.models import CachedRole
        from azurerbac.core.constants import RoleStatus
        from azurerbac.matching.models import RoleNetPermissions
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get_allowing_roles.return_value = None

        # Need at least one role for caching to happen
        role = RoleDefinition.model_validate(
            {
                "name": "role1",
                "properties": {
                    "roleName": "Test Role",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["*"]}],
                },
            }
        )
        mock_app_cache.get_roles_for_operation.return_value = ["role1"]
        mock_app_cache.get_role_by_id.return_value = CachedRole(
            definition=role,
            status=RoleStatus.ACTIVE,
        )
        mock_app_cache.get_role_coverage.return_value = (
            {"microsoft.storage/read"},
            set(),
        )
        mock_app_cache.get_role_net_permissions.return_value = RoleNetPermissions(1, 0)

        get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)

        # Should cache the result using lowered operation name as key
        mock_app_cache.set_allowing_roles.assert_called_once()
        cache_key = mock_app_cache.set_allowing_roles.call_args[0][0]
        assert cache_key == "microsoft.storage/read"

    def test_raises_on_missing_role_in_cache(self):
        """Test that RuntimeError is raised when role in index but not in cache."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get_allowing_roles.return_value = None
        mock_app_cache.get_roles_for_operation.return_value = ["role1"]
        mock_app_cache.get_role_by_id.return_value = None  # Role not in cache

        with pytest.raises(RuntimeError, match="role role1 in index but not in cache"):
            get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)

    def test_raises_on_missing_net_permissions(self):
        """Test that RuntimeError is raised when role missing net_perms."""
        from azurerbac.cache.models import CachedRole
        from azurerbac.core.constants import RoleStatus
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        mock_app_cache.get_allowing_roles.return_value = None
        mock_app_cache.get_roles_for_operation.return_value = ["role1"]

        role = RoleDefinition.model_validate(
            {
                "name": "role1",
                "properties": {
                    "roleName": "Test Role",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["*"]}],
                },
            }
        )
        mock_app_cache.get_role_by_id.return_value = CachedRole(
            definition=role,
            status=RoleStatus.ACTIVE,
        )
        mock_app_cache.get_role_net_permissions.return_value = None  # Missing net_perms

        with pytest.raises(RuntimeError, match="role role1 in index but missing net_perms"):
            get_roles_allowing_operation("Microsoft.Storage/read", False, mock_app_cache)


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
        assert result.diff.changed is True
        assert len(result.diff.changes) == 1

    def test_created_on_normalized_in_diff_json(self):
        """Test that createdOn is normalized to avoid showing as a diff.

        Azure API sometimes returns different createdOn values for the same role.
        The before_json should have its createdOn set to match after_json.
        """
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=5,
            role_id="test-id",
            role_name="Test Role",
            event_type=EventType.UPDATED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Updated",
            diff_json={
                "changed": True,
                "changes": [{"path": "description", "from": "old", "to": "new"}],
                "before_json": {
                    "id": "test-id",
                    "name": "test-guid",
                    "properties": {
                        "roleName": "Test Role",
                        "createdOn": "2025-11-18T16:10:13.262Z",  # Different createdOn
                        "updatedOn": "2025-12-01T00:00:00Z",
                    },
                },
                "after_json": {
                    "id": "test-id",
                    "name": "test-guid",
                    "properties": {
                        "roleName": "Test Role",
                        "createdOn": "2025-11-17T16:01:32.566Z",  # Different createdOn
                        "updatedOn": "2025-12-17T00:00:00Z",
                    },
                },
            },
            role_json=None,
        )

        result = enrich_event_with_diff(event)

        # Both before and after should have the same createdOn (normalized to after's value)
        before_created = result.diff.before_json["properties"]["createdOn"]
        after_created = result.diff.after_json["properties"]["createdOn"]
        assert before_created == after_created
        # The value should be from the after_json
        assert after_created == "2025-11-17T16:01:32.566Z"

    def test_changes_only_diff_not_modified(self):
        """Test that diff_json with only 'changes' (no before_json/after_json) is preserved.

        Delete events created by diff_roles(old, None) only have 'changes' - not the full JSON.
        The function should NOT introduce before_json/after_json keys in this case.
        """
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=6,
            role_id="deleted-role",
            role_name="Deleted Role",
            event_type=EventType.DELETED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Role deleted",
            diff_json={
                "changed": True,
                "changes": [{"path": "<root>", "from": {"id": "test"}, "to": None}],
                # No before_json or after_json - this is how delete events are stored
            },
            role_json=None,
        )

        result = enrich_event_with_diff(event)

        # Should preserve the original structure - before_json/after_json should be None
        assert result.diff.before_json is None
        assert result.diff.after_json is None
        # changes should still be present
        assert result.diff.changed is True
        assert len(result.diff.changes) == 1

    def test_created_on_normalized_when_after_json_is_none(self):
        """Test createdOn normalization when after_json is None.

        E.g., delete scenario with full JSON.
        Bug regression test: If after_json is missing but before_json has createdOn,
        the value should still be preserved (not cause issues).
        """
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=7,
            role_id="deleted-role",
            role_name="Deleted Role",
            event_type=EventType.DELETED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Role deleted",
            diff_json={
                "changed": True,
                "changes": [{"path": "<root>", "from": {"id": "test"}, "to": None}],
                "before_json": {
                    "id": "test-id",
                    "name": "test-guid",
                    "properties": {
                        "roleName": "Deleted Role",
                        "createdOn": "2025-11-18T16:10:13.262Z",
                    },
                },
                "after_json": None,  # Role was deleted
            },
            role_json=None,
        )

        result = enrich_event_with_diff(event)

        # before_json should still have createdOn (fallback to before's value)
        before_created = result.diff.before_json["properties"]["createdOn"]
        assert before_created == "2025-11-18T16:10:13.262Z"
        # after_json should remain None
        assert result.diff.after_json is None

    def test_created_on_normalized_when_before_missing_properties(self):
        """Test createdOn normalization when before_json has no properties key.

        Bug regression test: If before_json exists but has no 'properties' key,
        and after_json has createdOn, the function should add properties to before.
        """
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = CachedChangeEvent(
            id=8,
            role_id="test-id",
            role_name="Test Role",
            event_type=EventType.UPDATED,
            scan_timestamp=None,
            azure_updated_on=None,
            summary="Updated",
            diff_json={
                "changed": True,
                "changes": [{"path": "properties", "from": None, "to": {"roleName": "Test"}}],
                "before_json": {
                    "id": "test-id",
                    "name": "test-guid",
                    # No properties key
                },
                "after_json": {
                    "id": "test-id",
                    "name": "test-guid",
                    "properties": {
                        "roleName": "Test Role",
                        "createdOn": "2025-11-17T16:01:32.566Z",
                    },
                },
            },
            role_json=None,
        )

        result = enrich_event_with_diff(event)

        # before_json should now have properties with createdOn added
        assert "properties" in result.diff.before_json
        before_created = result.diff.before_json["properties"]["createdOn"]
        after_created = result.diff.after_json["properties"]["createdOn"]
        assert before_created == after_created
        assert after_created == "2025-11-17T16:01:32.566Z"


# =============================================================================
# Helper to build a CachedRole with custom scopes / conditions
# =============================================================================


def _make_cached_role_full(
    role_id: str,
    role_name: str,
    *,
    actions: list[str] | None = None,
    data_actions: list[str] | None = None,
    assignable_scopes: list[str] | None = None,
    condition: str | None = None,
    status: RoleStatus = RoleStatus.ACTIVE,
) -> CachedRole:
    """Create a CachedRole with full control over scopes and conditions."""
    permission_dict: dict = {
        "actions": actions or [],
        "notActions": [],
        "dataActions": data_actions or [],
        "notDataActions": [],
    }
    if condition:
        permission_dict["condition"] = condition

    definition = RoleDefinition.model_validate(
        {
            "name": role_id,
            "id": f"/providers/{ROLE_DEFINITION_TYPE}/{role_id}",
            "type": ROLE_DEFINITION_TYPE,
            "properties": {
                "roleName": role_name,
                "type": "BuiltInRole",
                "description": f"Test role: {role_name}",
                "permissions": [permission_dict],
                "assignableScopes": assignable_scopes or ["/"],
            },
        }
    )
    return CachedRole(definition=definition, status=status)


# =============================================================================
# Tests for related-role helper functions
# =============================================================================


class TestJaccard:
    """Tests for _jaccard similarity function."""

    @pytest.mark.parametrize(
        ("set_a", "set_b", "expected"),
        [
            pytest.param(frozenset(), frozenset(), 1.0, id="both_empty"),
            pytest.param(
                frozenset({"a", "b", "c"}), frozenset({"a", "b", "c"}), 1.0, id="identical"
            ),
            pytest.param(frozenset({"a"}), frozenset({"b"}), 0.0, id="disjoint"),
            pytest.param(
                frozenset({"a", "b", "c"}), frozenset({"b", "c", "d"}), 0.5, id="partial_overlap"
            ),
            pytest.param(frozenset({"a"}), frozenset(), 0.0, id="one_empty"),
            pytest.param(frozenset({"a", "b"}), frozenset({"a", "b", "c"}), 2 / 3, id="subset"),
        ],
    )
    def test_jaccard_similarity(self, set_a: frozenset, set_b: frozenset, expected: float):
        """Test Jaccard similarity for various set combinations."""
        from azurerbac.web.services.pages import _jaccard

        assert _jaccard(set_a, set_b) == pytest.approx(expected)


class TestConditionSimilarity:
    """Tests for _condition_similarity function."""

    @pytest.mark.parametrize(
        ("conds_a", "conds_b", "expected"),
        [
            pytest.param(frozenset(), frozenset(), 1.0, id="both_empty"),
            pytest.param(frozenset(), frozenset({"cond1"}), 0.0, id="first_empty"),
            pytest.param(frozenset({"cond1"}), frozenset(), 0.0, id="second_empty"),
            pytest.param(
                frozenset({"@Resource[Microsoft.Storage/storageAccounts/blobServices]"}),
                frozenset({"@Resource[Microsoft.Storage/storageAccounts/blobServices]"}),
                1.0,
                id="identical",
            ),
            pytest.param(
                frozenset({"condA", "condB"}),
                frozenset({"condB", "condC"}),
                1 / 3,
                id="partial_overlap",
            ),
        ],
    )
    def test_condition_similarity(self, conds_a: frozenset, conds_b: frozenset, expected: float):
        """Test condition similarity for various condition combinations."""
        from azurerbac.web.services.pages import _condition_similarity

        assert _condition_similarity(conds_a, conds_b) == pytest.approx(expected)


class TestExtractRoleMetadata:
    """Tests for _extract_role_metadata function."""

    def test_default_scope(self):
        from azurerbac.web.services.pages import _extract_role_metadata

        role = make_cached_role("r1", "Reader")
        scopes, conditions = _extract_role_metadata(role)
        assert scopes == frozenset(("/",))
        assert conditions == frozenset()

    def test_custom_scopes(self):
        from azurerbac.web.services.pages import _extract_role_metadata

        role = _make_cached_role_full(
            "r1",
            "Custom",
            assignable_scopes=["/subscriptions/abc", "/subscriptions/def"],
        )
        scopes, _conditions = _extract_role_metadata(role)
        assert scopes == frozenset({"/subscriptions/abc", "/subscriptions/def"})

    def test_conditions_extracted(self):
        from azurerbac.web.services.pages import _extract_role_metadata

        role = _make_cached_role_full(
            "r1",
            "Conditional",
            actions=["Microsoft.Storage/*/read"],
            condition="@Resource[Microsoft.Storage/storageAccounts:kind] == 'BlobStorage'",
        )
        _, conditions = _extract_role_metadata(role)
        assert len(conditions) == 1
        assert "BlobStorage" in next(iter(conditions))

    def test_no_conditions_when_absent(self):
        from azurerbac.web.services.pages import _extract_role_metadata

        role = _make_cached_role_full("r1", "Plain", actions=["*/read"])
        _, conditions = _extract_role_metadata(role)
        assert conditions == frozenset()


class TestScopesContain:
    """Tests for _scopes_contain function."""

    @pytest.mark.parametrize(
        ("broader", "narrower", "expected"),
        [
            pytest.param(frozenset(("/",)), frozenset(("/",)), True, id="identical"),
            pytest.param(
                frozenset(("/",)),
                frozenset(("/subscriptions/abc",)),
                True,
                id="root_contains_narrow",
            ),
            pytest.param(
                frozenset(("/subscriptions/abc",)),
                frozenset(("/",)),
                False,
                id="narrow_not_contains_root",
            ),
            pytest.param(
                frozenset(("/subscriptions/abc",)),
                frozenset(("/subscriptions/abc/resourceGroups/rg1",)),
                True,
                id="prefix_containment",
            ),
            pytest.param(
                frozenset(("/subscriptions/abc/resourceGroups/rg1",)),
                frozenset(("/subscriptions/abc",)),
                False,
                id="prefix_containment_reverse",
            ),
            pytest.param(
                frozenset(("/subscriptions/abc",)),
                frozenset(("/subscriptions/xyz",)),
                False,
                id="disjoint",
            ),
            pytest.param(
                frozenset(("/Subscriptions/ABC",)),
                frozenset(("/subscriptions/abc/resourceGroups/rg1",)),
                True,
                id="case_insensitive",
            ),
            pytest.param(
                frozenset(("/subscriptions/abc", "/subscriptions/xyz")),
                frozenset(("/subscriptions/abc/resourceGroups/rg1",)),
                True,
                id="multiple_broader",
            ),
            pytest.param(frozenset(), frozenset(), True, id="both_empty"),
        ],
    )
    def test_scopes_contain(self, broader: frozenset, narrower: frozenset, expected: bool):
        """Test scope containment for various scope combinations."""
        from azurerbac.web.services.pages import _scopes_contain

        assert _scopes_contain(broader, narrower) is expected


# =============================================================================
# Tests for compute_related_roles
# =============================================================================


def _build_cache_service(
    roles: dict[str, CachedRole],
    coverage: dict[str, RoleCoverage],
    op_to_roles: dict[str, list[str]],
) -> MagicMock:
    """Build a MagicMock CacheService with the given data."""
    mock = MagicMock()
    mock.get_role_by_id.side_effect = lambda rid: roles.get(rid)
    mock.get_role_coverage.side_effect = lambda rid: coverage.get(rid)
    mock.get_related_roles.return_value = None  # cache miss by default
    mock.get_comparison.return_value = None  # cache miss by default
    mock.restore_operation_casing.side_effect = lambda ops: list(ops)
    mock.cache.operation_to_roles = op_to_roles
    mock.cache.ops_lowered_to_orig = {}
    return mock


class TestComputeRelatedRoles:
    """Tests for compute_related_roles function."""

    def test_role_not_found_returns_empty(self):
        from azurerbac.web.services.pages import compute_related_roles

        cache = _build_cache_service({}, {}, {})
        assert compute_related_roles("missing-id", cache=cache) == []

    def test_no_coverage_returns_empty(self):
        from azurerbac.web.services.pages import compute_related_roles

        role = make_cached_role("r1", "Reader")
        cache = _build_cache_service({"r1": role}, {}, {})
        assert compute_related_roles("r1", cache=cache) == []

    def test_empty_operations_returns_empty(self):
        from azurerbac.web.services.pages import compute_related_roles

        role = make_cached_role("r1", "Reader")
        coverage = RoleCoverage(control=set(), data=set())
        cache = _build_cache_service({"r1": role}, {"r1": coverage}, {})
        assert compute_related_roles("r1", cache=cache) == []

    def test_no_co_occurring_roles_returns_empty(self):
        from azurerbac.web.services.pages import compute_related_roles

        role = make_cached_role("r1", "Reader")
        coverage = RoleCoverage(control={"op1"}, data=set())
        # op1 maps only to r1 itself
        cache = _build_cache_service({"r1": role}, {"r1": coverage}, {"op1": ["r1"]})
        assert compute_related_roles("r1", cache=cache) == []

    def test_basic_related_role(self):
        """Two roles sharing all operations should have high similarity."""
        from azurerbac.web.services.pages import compute_related_roles

        ops = {"op1", "op2", "op3"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        op_to_roles = {op: ["r1", "r2"] for op in ops}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].role_id == "r2"
        # Same ops, same scope (/), same conditions (none) → 0.90*1 + 0.05*1 + 0.05*1 = 1.0
        assert results[0].similarity == pytest.approx(1.0)
        assert results[0].shared_count == 3
        assert results[0].total_count == 3

    def test_partial_overlap_similarity(self):
        """Partial operation overlap should produce fractional similarity."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"op1", "op2", "op3", "op4"}, data=set())
        cov2 = RoleCoverage(control={"op1", "op2", "op5", "op6"}, data=set())
        op_to_roles = {
            "op1": ["r1", "r2"],
            "op2": ["r1", "r2"],
            "op3": ["r1"],
            "op4": ["r1"],
            "op5": ["r2"],
            "op6": ["r2"],
        }
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        # Jaccard: intersection=2, union=6 → 1/3
        # Same scope → 1.0, same conditions (none) → 1.0
        # 0.90*(1/3) + 0.05*1 + 0.05*1 = 0.3 + 0.1 = 0.4
        expected = 0.90 * (2 / 6) + 0.05 * 1.0 + 0.05 * 1.0
        assert results[0].similarity == pytest.approx(expected)

    def test_below_threshold_filtered(self):
        """Roles below 20% composite similarity should be excluded."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        r2 = _make_cached_role_full(
            "r2",
            "Beta",
            actions=["x"],
            assignable_scopes=["/subscriptions/different"],
        )
        # Tiny overlap: 1 shared out of many → low Jaccard
        # Different scopes → scope_sim = 0.0
        # One conditioned, one not → cond_sim mismatch handled
        many_ops = {f"op{i}" for i in range(20)}
        cov1 = RoleCoverage(control=many_ops | {"shared_op"}, data=set())
        cov2 = RoleCoverage(control={f"uniq{i}" for i in range(20)} | {"shared_op"}, data=set())
        op_to_roles: dict[str, list[str]] = {"shared_op": ["r1", "r2"]}
        for op in many_ops:
            op_to_roles[op] = ["r1"]
        for i in range(20):
            op_to_roles[f"uniq{i}"] = ["r2"]
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        # intersection=1, union=41 → ops_sim ≈ 0.024
        # scope_sim=0.0, cond_sim=1.0 → 0.90*0.024 + 0.05*0 + 0.05*1 = 0.072 < 0.20
        assert results == []

    def test_deleted_roles_excluded(self):
        """Deleted roles should not appear in results."""
        from azurerbac.web.services.pages import compute_related_roles

        ops = {"op1", "op2"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = _make_cached_role_full("r2", "Deleted", actions=["x"], status=RoleStatus.DELETED)
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        op_to_roles = {op: ["r1", "r2"] for op in ops}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert results == []

    def test_results_sorted_descending(self):
        """Results should be sorted by similarity from highest to lowest."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        r_high = make_cached_role("r2", "High")
        r_mid = make_cached_role("r3", "Medium")

        cov1 = RoleCoverage(control={"a", "b", "c", "d"}, data=set())
        # r2 shares 4/4 ops → high similarity
        cov_high = RoleCoverage(control={"a", "b", "c", "d"}, data=set())
        # r3 shares 2/4 ops → medium similarity
        cov_mid = RoleCoverage(control={"a", "b", "x", "y"}, data=set())

        op_to_roles = {
            "a": ["r1", "r2", "r3"],
            "b": ["r1", "r2", "r3"],
            "c": ["r1", "r2"],
            "d": ["r1", "r2"],
            "x": ["r3"],
            "y": ["r3"],
        }
        cache = _build_cache_service(
            {"r1": r1, "r2": r_high, "r3": r_mid},
            {"r1": cov1, "r2": cov_high, "r3": cov_mid},
            op_to_roles,
        )

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 2
        assert results[0].role_id == "r2"
        assert results[1].role_id == "r3"
        assert results[0].similarity > results[1].similarity

    def test_limit_respected(self):
        """Results should be capped at the limit parameter."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        roles = {"r1": r1}
        coverages = {"r1": RoleCoverage(control={"shared"}, data=set())}
        op_to_roles: dict[str, list[str]] = {"shared": ["r1"]}

        # Create 10 roles that share operations with r1
        for i in range(2, 12):
            rid = f"r{i}"
            roles[rid] = make_cached_role(rid, f"Role{i}")
            coverages[rid] = RoleCoverage(control={"shared"}, data=set())
            op_to_roles["shared"].append(rid)

        cache = _build_cache_service(roles, coverages, op_to_roles)

        results = compute_related_roles("r1", limit=3, cache=cache)
        assert len(results) <= 3

    def test_scope_mismatch_reduces_similarity(self):
        """Different assignable scopes should reduce similarity."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"])
        r_same_scope = _make_cached_role_full("r2", "SameScope", actions=["op1"])
        r_diff_scope = _make_cached_role_full(
            "r3",
            "DiffScope",
            actions=["op1"],
            assignable_scopes=["/subscriptions/xyz"],
        )

        ops = {"op1"}
        cov1 = RoleCoverage(control=ops, data=set())
        cov_same = RoleCoverage(control=ops, data=set())
        cov_diff = RoleCoverage(control=ops, data=set())

        op_to_roles = {"op1": ["r1", "r2", "r3"]}
        cache = _build_cache_service(
            {"r1": r1, "r2": r_same_scope, "r3": r_diff_scope},
            {"r1": cov1, "r2": cov_same, "r3": cov_diff},
            op_to_roles,
        )

        results = compute_related_roles("r1", cache=cache)
        same_scope_result = next(r for r in results if r.role_id == "r2")
        diff_scope_result = next(r for r in results if r.role_id == "r3")
        assert same_scope_result.similarity > diff_scope_result.similarity

    def test_condition_mismatch_reduces_similarity(self):
        """One role with conditions vs one without should reduce similarity."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"])
        r_no_cond = _make_cached_role_full("r2", "NoCond", actions=["op1"])
        r_with_cond = _make_cached_role_full(
            "r3",
            "WithCond",
            actions=["op1"],
            condition="@Resource[Microsoft.Storage/storageAccounts:kind] == 'BlobStorage'",
        )

        ops = {"op1"}
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        cov3 = RoleCoverage(control=ops, data=set())

        op_to_roles = {"op1": ["r1", "r2", "r3"]}
        cache = _build_cache_service(
            {"r1": r1, "r2": r_no_cond, "r3": r_with_cond},
            {"r1": cov1, "r2": cov2, "r3": cov3},
            op_to_roles,
        )

        results = compute_related_roles("r1", cache=cache)
        no_cond_result = next(r for r in results if r.role_id == "r2")
        with_cond_result = next(r for r in results if r.role_id == "r3")
        # r1 has no condition, r3 has condition → cond_sim = 0.0 → lower
        assert no_cond_result.similarity > with_cond_result.similarity

    def test_data_actions_included_in_similarity(self):
        """Data actions should contribute to operation overlap."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")

        # r1 and r2 share only data actions
        cov1 = RoleCoverage(control=set(), data={"data_op1", "data_op2"})
        cov2 = RoleCoverage(control=set(), data={"data_op1", "data_op2"})

        op_to_roles = {"data_op1": ["r1", "r2"], "data_op2": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].role_id == "r2"
        assert results[0].similarity == pytest.approx(1.0)

    def test_related_role_dataclass_fields(self):
        """Verify all fields of the RelatedRole dataclass are populated."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")

        cov1 = RoleCoverage(control={"op1", "op2"}, data=set())
        cov2 = RoleCoverage(control={"op1", "op2", "op3"}, data=set())

        op_to_roles = {"op1": ["r1", "r2"], "op2": ["r1", "r2"], "op3": ["r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        result = results[0]
        assert result.role_id == "r2"
        assert result.role_name == "Beta"
        assert result.shared_count == 2
        assert result.total_count == 3
        # Jaccard: 2/3 → 0.90*(2/3) + 0.05*1 + 0.05*1 = 0.7
        expected_sim = 0.90 * (2 / 3) + 0.05 * 1.0 + 0.05 * 1.0
        assert result.similarity == pytest.approx(expected_sim)

    def test_result_cached_on_second_call(self):
        """Second call should return cached result without recomputing."""
        from azurerbac.web.services.pages import compute_related_roles

        ops = {"op1", "op2"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        op_to_roles = {op: ["r1", "r2"] for op in ops}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        # First call computes and stores result via the cache implementation
        first = compute_related_roles("r1", limit=1, cache=cache)
        assert len(first) == 1

        # Simulate that the cache now returns the computed result on lookup
        cache.get_related_roles.reset_mock()
        cache.get_related_roles.return_value = first

        # Record expensive call count before second invocation
        coverage_calls_before = cache.get_role_coverage.call_count

        # Second call should hit cache, not recompute
        second = compute_related_roles("r1", limit=1, cache=cache)
        assert second == first
        cache.get_related_roles.assert_called_once_with("r1")
        assert cache.get_role_coverage.call_count == coverage_calls_before

    def test_cache_miss_then_stores(self):
        """First call should store computed result in cache."""
        from azurerbac.web.services.pages import compute_related_roles

        ops = {"op1"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        cache.set_related_roles.assert_called_once_with("r1", results)

    def test_subset_same_ops_same_scope_same_conditions(self):
        """Other has fewer ops, all in current, same scope/conditions → subset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1", "op2", "op3"])
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1", "op2"])
        cov1 = RoleCoverage(control={"op1", "op2", "op3"}, data=set())
        cov2 = RoleCoverage(control={"op1", "op2"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"], "op2": ["r1", "r2"], "op3": ["r1"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].is_subset is True
        assert results[0].is_superset is False

    def test_superset_same_ops_same_scope_same_conditions(self):
        """Other has more ops, all current ops in other, same scope/conditions → superset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1", "op2"])
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1", "op2", "op3"])
        cov1 = RoleCoverage(control={"op1", "op2"}, data=set())
        cov2 = RoleCoverage(control={"op1", "op2", "op3"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"], "op2": ["r1", "r2"], "op3": ["r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].is_superset is True
        assert results[0].is_subset is False

    def test_subset_narrower_scope(self):
        """Other has same ops but narrower scope → subset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"], assignable_scopes=["/"])
        r2 = _make_cached_role_full(
            "r2", "Beta", actions=["op1"], assignable_scopes=["/subscriptions/abc"]
        )
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        # r2 has narrower scope, so from r1's POV: r2 is a subset
        assert results[0].is_subset is True
        # r2 is NOT a superset (it has narrower scope)
        assert results[0].is_superset is False

    def test_superset_broader_scope(self):
        """Other has same ops but broader scope → superset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full(
            "r1", "Alpha", actions=["op1"], assignable_scopes=["/subscriptions/abc"]
        )
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1"], assignable_scopes=["/"])
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        # r2 has broader scope, so from r1's POV: r2 is a superset
        assert results[0].is_superset is True
        assert results[0].is_subset is False

    def test_different_conditions_neither_subset_nor_superset(self):
        """Same ops but different conditions → neither subset nor superset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full(
            "r1", "Alpha", actions=["op1"], condition="@Resource[Microsoft.Storage:kind] == 'Blob'"
        )
        r2 = _make_cached_role_full(
            "r2", "Beta", actions=["op1"], condition="@Resource[Microsoft.Storage:kind] == 'Table'"
        )
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].is_subset is False
        assert results[0].is_superset is False

    def test_one_conditioned_one_not_neither(self):
        """One role has conditions, other doesn't → neither."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"])
        r2 = _make_cached_role_full(
            "r2", "Beta", actions=["op1"], condition="@Resource[Microsoft.Storage:kind] == 'Blob'"
        )
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].is_subset is False
        assert results[0].is_superset is False

    def test_equal_roles_both_subset_and_superset(self):
        """Same ops, same scope, same conditions → both subset and superset."""
        from azurerbac.web.services.pages import compute_related_roles

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"])
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1"])
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        op_to_roles = {"op1": ["r1", "r2"]}
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, op_to_roles)

        results = compute_related_roles("r1", cache=cache)
        assert len(results) == 1
        assert results[0].is_subset is True
        assert results[0].is_superset is True


# =============================================================================
# Tests for compute_role_comparison
# =============================================================================


class TestComputeRoleComparison:
    """Tests for compute_role_comparison function."""

    def test_role_a_not_found_returns_none(self):
        from azurerbac.comparer import compute_role_comparison

        cache = _build_cache_service({}, {}, {})
        assert compute_role_comparison("missing", "also-missing", cache=cache) is None

    def test_role_b_not_found_returns_none(self):
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        cache = _build_cache_service({"r1": r1}, {}, {})
        assert compute_role_comparison("r1", "missing", cache=cache) is None

    def test_identical_roles_all_shared(self):
        """Two roles with identical operations should have no unique ops."""
        from azurerbac.comparer import compute_role_comparison

        ops = {"op1", "op2", "op3"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.only_a_control == []
        assert result.only_a_data == []
        assert sorted(result.shared_control) == sorted(ops)
        assert result.shared_data == []
        assert result.only_b_control == []
        assert result.only_b_data == []

    def test_disjoint_roles_no_shared(self):
        """Two roles with no overlap should have no shared ops."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"op1", "op2"}, data=set())
        cov2 = RoleCoverage(control={"op3", "op4"}, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert sorted(result.only_a_control) == ["op1", "op2"]
        assert result.shared_control == []
        assert sorted(result.only_b_control) == ["op3", "op4"]

    def test_partial_overlap(self):
        """Partial overlap should correctly split into three sets."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"shared", "only_a"}, data={"d_shared", "d_only_a"})
        cov2 = RoleCoverage(control={"shared", "only_b"}, data={"d_shared", "d_only_b"})
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.only_a_control == ["only_a"]
        assert result.shared_control == ["shared"]
        assert result.only_b_control == ["only_b"]
        assert result.only_a_data == ["d_only_a"]
        assert result.shared_data == ["d_shared"]
        assert result.only_b_data == ["d_only_b"]

    def test_no_coverage_treated_as_empty(self):
        """Roles without coverage should be treated as having no operations."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"op1"}, data=set())
        # r2 has no coverage entry
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.only_a_control == ["op1"]
        assert result.shared_control == []
        assert result.only_b_control == []

    def test_side_metadata_populated(self):
        """RoleComparisonSide fields should be populated correctly."""
        from azurerbac.comparer import compute_role_comparison

        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"], data_actions=["d1"])
        r2 = _make_cached_role_full("r2", "Beta", actions=["op2"])
        cov1 = RoleCoverage(control={"op1"}, data={"d1"})
        cov2 = RoleCoverage(control={"op2"}, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.role_a.role_id == "r1"
        assert result.role_a.role_name == "Alpha"
        assert result.role_a.control_count == 1
        assert result.role_a.data_count == 1
        assert result.role_b.role_id == "r2"
        assert result.role_b.role_name == "Beta"
        assert result.role_b.control_count == 1
        assert result.role_b.data_count == 0

    def test_results_are_sorted(self):
        """Operation lists should be alphabetically sorted."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"z_op", "a_op", "m_op"}, data=set())
        cov2 = RoleCoverage(control=set(), data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.only_a_control == ["a_op", "m_op", "z_op"]

    def test_same_role_returns_none(self):
        """Comparing a role with itself should return None."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        cache = _build_cache_service({"r1": r1}, {}, {})
        assert compute_role_comparison("r1", "r1", cache=cache) is None

    def test_cache_hit_avoids_recompute(self):
        """Second call with same IDs should return cached result."""
        from azurerbac.comparer import compute_role_comparison

        ops = {"op1", "op2"}
        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control=ops, data=set())
        cov2 = RoleCoverage(control=ops, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        first = compute_role_comparison("r1", "r2", cache=cache)
        assert first is not None
        # Verify it was stored
        cache.set_comparison.assert_called_once()

        # Now simulate a cache hit on second call
        cache.get_comparison.return_value = first
        second = compute_role_comparison("r1", "r2", cache=cache)
        assert second is first

    def test_reverse_order_separate_cache(self):
        """Calling with (B, A) produces a separate cache entry with swapped sides."""
        from azurerbac.comparer import compute_role_comparison

        r1 = make_cached_role("r1", "Alpha")
        r2 = make_cached_role("r2", "Beta")
        cov1 = RoleCoverage(control={"op1", "shared"}, data=set())
        cov2 = RoleCoverage(control={"op2", "shared"}, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        # Forward order: A=r1, B=r2
        forward = compute_role_comparison("r1", "r2", cache=cache)
        assert forward is not None
        cache.set_comparison.assert_called_once_with("r1:r2", forward)
        assert forward.role_a.role_id == "r1"
        assert forward.role_b.role_id == "r2"

        # Reset mock and compute reverse order
        cache.get_comparison.return_value = None
        cache.set_comparison.reset_mock()
        reverse = compute_role_comparison("r2", "r1", cache=cache)
        assert reverse is not None
        cache.set_comparison.assert_called_once_with("r2:r1", reverse)
        assert reverse.role_a.role_id == "r2"
        assert reverse.role_b.role_id == "r1"

        # Verify operation sets are mirrored
        assert forward.only_a_control == reverse.only_b_control
        assert forward.only_b_control == reverse.only_a_control
        assert forward.shared_control == reverse.shared_control

    def test_conditions_populated_in_sides(self):
        """ABAC conditions should appear in role comparison sides."""
        from azurerbac.comparer import compute_role_comparison

        cond = "@Resource[Microsoft.Storage/storageAccounts:kind] == 'BlobStorage'"
        r1 = _make_cached_role_full("r1", "Alpha", actions=["op1"], condition=cond)
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1"])
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.role_a.conditions == [cond]
        assert result.role_b.conditions == []

    def test_assignable_scopes_populated_in_sides(self):
        """Assignable scopes should appear in role comparison sides."""
        from azurerbac.comparer import compute_role_comparison

        r1 = _make_cached_role_full(
            "r1", "Alpha", actions=["op1"], assignable_scopes=["/subscriptions/abc"]
        )
        r2 = _make_cached_role_full("r2", "Beta", actions=["op1"])
        cov1 = RoleCoverage(control={"op1"}, data=set())
        cov2 = RoleCoverage(control={"op1"}, data=set())
        cache = _build_cache_service({"r1": r1, "r2": r2}, {"r1": cov1, "r2": cov2}, {})

        result = compute_role_comparison("r1", "r2", cache=cache)
        assert result is not None
        assert result.role_a.assignable_scopes == ["/subscriptions/abc"]
        assert result.role_b.assignable_scopes == ["/"]


# =============================================================================
# _build_popular_comparisons (cache/build.py)
# =============================================================================


class TestBuildPopularComparisons:
    """Tests for _build_popular_comparisons in the cache build pipeline."""

    # Well-known IDs from POPULAR_COMPARE_PAIRS (Reader, Contributor)
    READER_ID = "acdd72a7-3385-48ef-bd42-f606fba81ae7"
    CONTRIBUTOR_ID = "b24988ac-6180-42a0-ab88-20f7382dd24c"

    def test_resolves_known_pairs(self):
        """Pairs whose IDs exist in roles_by_id should be resolved."""
        from azurerbac.cache.build import _build_popular_comparisons

        roles_by_id = {
            self.READER_ID: make_cached_role(self.READER_ID, "Reader"),
            self.CONTRIBUTOR_ID: make_cached_role(self.CONTRIBUTOR_ID, "Contributor"),
        }

        result = _build_popular_comparisons(roles_by_id)
        pair = next(
            (
                p
                for p in result
                if p.role_a_id == self.READER_ID and p.role_b_id == self.CONTRIBUTOR_ID
            ),
            None,
        )
        assert pair is not None
        assert pair.role_a_name == "Reader"
        assert pair.role_b_name == "Contributor"
        assert pair.category == "General"

    def test_skips_pair_when_role_missing(self):
        """Pairs where one role is missing should be skipped."""
        from azurerbac.cache.build import _build_popular_comparisons

        roles_by_id = {
            self.READER_ID: make_cached_role(self.READER_ID, "Reader"),
        }

        result = _build_popular_comparisons(roles_by_id)
        assert all(p.role_b_id != self.CONTRIBUTOR_ID for p in result)

    def test_empty_roles_returns_empty(self):
        """Empty roles_by_id should produce an empty list."""
        from azurerbac.cache.build import _build_popular_comparisons

        result = _build_popular_comparisons({})
        assert result == []

    def test_all_pairs_resolved_with_full_index(self):
        """When all role IDs exist, all pairs should resolve."""
        from azurerbac.cache.build import _build_popular_comparisons
        from azurerbac.core.constants import POPULAR_COMPARE_PAIRS

        roles_by_id: dict[str, CachedRole] = {}
        for id_a, id_b, _cat in POPULAR_COMPARE_PAIRS:
            for rid in (id_a, id_b):
                if rid not in roles_by_id:
                    roles_by_id[rid] = make_cached_role(rid, f"Role-{rid[:8]}")

        result = _build_popular_comparisons(roles_by_id)
        assert len(result) == len(POPULAR_COMPARE_PAIRS)

    def test_returned_objects_are_frozen(self):
        """PopularComparison instances should be immutable."""
        from azurerbac.cache.build import _build_popular_comparisons

        roles_by_id = {
            self.READER_ID: make_cached_role(self.READER_ID, "Reader"),
            self.CONTRIBUTOR_ID: make_cached_role(self.CONTRIBUTOR_ID, "Contributor"),
        }

        result = _build_popular_comparisons(roles_by_id)
        if result:
            with pytest.raises(AttributeError):
                result[0].category = "modified"  # type: ignore[misc]


# =============================================================================
# Tests for build_permission_timeline
# =============================================================================


class TestBuildPermissionTimeline:
    """Tests for build_permission_timeline function."""

    def _make_event(
        self,
        event_type: str,
        role_json: dict | None = None,
        scan_timestamp: str = "2025-06-01T00:00:00+00:00",
        azure_updated_on: str | None = None,
    ) -> CachedChangeEvent:
        """Create a CachedChangeEvent for testing."""
        import datetime as dt

        ts = dt.datetime.fromisoformat(scan_timestamp)
        azure_ts = dt.datetime.fromisoformat(azure_updated_on) if azure_updated_on else None
        return CachedChangeEvent(
            id=1,
            role_id="test-id",
            role_name="Test Role",
            event_type=event_type,
            scan_timestamp=ts,
            azure_updated_on=azure_ts,
            summary="test",
            diff_json=None,
            role_json=role_json,
        )

    def _make_role_json(
        self,
        actions: list[str] | None = None,
        data_actions: list[str] | None = None,
    ) -> dict:
        """Create a minimal role_json with given permission patterns."""
        return {
            "id": "test-id",
            "name": "test-guid",
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {
                "roleName": "Test Role",
                "type": "BuiltInRole",
                "permissions": [
                    {
                        "actions": actions or [],
                        "notActions": [],
                        "dataActions": data_actions or [],
                        "notDataActions": [],
                    }
                ],
                "assignableScopes": ["/"],
            },
        }

    def test_empty_events_returns_empty(self):
        """No events should produce an empty timeline."""
        from azurerbac.web.services.pages import build_permission_timeline

        assert build_permission_timeline([]) == []

    def test_single_event_returns_single_point(self):
        """A single created event should produce one timeline point."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "created",
                self._make_role_json(actions=["Microsoft.Compute/*/read"]),
                scan_timestamp="2025-01-15T10:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert len(result) == 1
        assert result[0].actions == 1
        assert result[0].data_actions == 0
        assert result[0].total == 1
        assert result[0].event_type == "created"
        assert result[0].date == "2025-01-15"

    def test_multiple_versions_chronological_order(self):
        """Events are reversed to chronological order (oldest first)."""
        from azurerbac.web.services.pages import build_permission_timeline

        # Events come newest-first from the cache
        events = [
            self._make_event(
                "updated",
                self._make_role_json(actions=["a", "b", "c"]),
                azure_updated_on="2025-03-01T00:00:00+00:00",
                scan_timestamp="2025-03-02T00:00:00+00:00",
            ),
            self._make_event(
                "created",
                self._make_role_json(actions=["a"]),
                azure_updated_on="2025-01-01T00:00:00+00:00",
                scan_timestamp="2025-01-02T00:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert len(result) == 2
        # First point should be the oldest (created)
        assert result[0].event_type == "created"
        assert result[0].actions == 1
        # Second point should be the newer (updated)
        assert result[1].event_type == "updated"
        assert result[1].actions == 3

    def test_tracks_actions_and_data_actions_separately(self):
        """Actions and data actions should be counted separately."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "created",
                self._make_role_json(
                    actions=["Microsoft.Storage/*/read", "Microsoft.Storage/*/write"],
                    data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/read"],
                ),
            ),
        ]

        result = build_permission_timeline(events)
        assert len(result) == 1
        assert result[0].actions == 2
        assert result[0].data_actions == 1
        assert result[0].total == 3

    def test_delete_event_adds_zero_point(self):
        """Delete events should add a zero-point to show the drop-off."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "deleted",
                role_json=None,
                scan_timestamp="2025-06-01T00:00:00+00:00",
            ),
            self._make_event(
                "created",
                self._make_role_json(actions=["a", "b"]),
                scan_timestamp="2025-01-01T00:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert len(result) == 2
        assert result[0].actions == 2  # created
        assert result[1].total == 0  # deleted zero-point
        assert result[1].label == "Deleted"

    def test_skips_events_without_timestamp(self):
        """Events with no timestamp at all are skipped."""
        from azurerbac.web.services.pages import build_permission_timeline

        event = CachedChangeEvent(
            id=1,
            role_id="test-id",
            role_name="Test Role",
            event_type="created",
            scan_timestamp=None,
            azure_updated_on=None,
            summary="test",
            diff_json=None,
            role_json=self._make_role_json(actions=["a"]),
        )

        result = build_permission_timeline([event])
        assert len(result) == 0

    def test_prefers_azure_updated_on_for_date(self):
        """azure_updated_on should be preferred over scan_timestamp."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "updated",
                self._make_role_json(actions=["a"]),
                azure_updated_on="2025-05-15T00:00:00+00:00",
                scan_timestamp="2025-05-16T00:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert result[0].date == "2025-05-15"

    def test_version_numbers_are_sequential(self):
        """Version numbers should be sequential starting from 1."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "updated",
                self._make_role_json(actions=["a", "b", "c"]),
                scan_timestamp="2025-03-01T00:00:00+00:00",
            ),
            self._make_event(
                "updated",
                self._make_role_json(actions=["a", "b"]),
                scan_timestamp="2025-02-01T00:00:00+00:00",
            ),
            self._make_event(
                "created",
                self._make_role_json(actions=["a"]),
                scan_timestamp="2025-01-01T00:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert [pt.version for pt in result] == [1, 2, 3]

    def test_to_dict_serialization(self):
        """to_dict should produce a JSON-safe dictionary."""
        from azurerbac.web.services.models import PermissionTimelinePoint

        pt = PermissionTimelinePoint(
            date="2025-01-15",
            version=1,
            event_type="created",
            actions=5,
            data_actions=3,
            total=8,
            effective_actions=50,
            effective_data_actions=30,
            effective_total=80,
            label="Created",
        )

        d = pt.to_dict()
        assert d == {
            "date": "2025-01-15",
            "version": 1,
            "event_type": "created",
            "actions": 5,
            "data_actions": 3,
            "total": 8,
            "effective_actions": 50,
            "effective_data_actions": 30,
            "effective_total": 80,
            "label": "Created",
        }

    def test_initial_scan_label(self):
        """Initial scan events should get the correct label."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "initial_scan",
                self._make_role_json(actions=["a"]),
            ),
        ]

        result = build_permission_timeline(events)
        assert result[0].label == "Initial scan"

    def test_multiple_permission_blocks(self):
        """Roles with multiple permission blocks should sum all actions."""
        from azurerbac.web.services.pages import build_permission_timeline

        role_json = {
            "id": "test-id",
            "name": "test-guid",
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {
                "roleName": "Test Role",
                "permissions": [
                    {"actions": ["a", "b"], "dataActions": ["d1"]},
                    {"actions": ["c"], "dataActions": ["d2", "d3"]},
                ],
                "assignableScopes": ["/"],
            },
        }

        events = [self._make_event("created", role_json)]

        result = build_permission_timeline(events)
        assert result[0].actions == 3
        assert result[0].data_actions == 3
        assert result[0].total == 6

    def test_effective_counts_fallback_without_operations(self):
        """Without operations catalog, effective counts equal pattern counts."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "created",
                self._make_role_json(
                    actions=["Microsoft.Compute/*/read"],
                    data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/read"],
                ),
            ),
        ]

        result = build_permission_timeline(events, all_operations=None)
        assert result[0].actions == 1
        assert result[0].effective_actions == 1  # Falls back to pattern count
        assert result[0].data_actions == 1
        assert result[0].effective_data_actions == 1  # Falls back to pattern count
        assert result[0].effective_total == 2

    def test_effective_counts_with_operations_expand_wildcards(self):
        """With operations catalog, wildcards expand to effective counts."""
        from azurerbac.azure.models import OperationData
        from azurerbac.web.services.pages import build_permission_timeline

        # Create a small operations catalog
        ops = [
            OperationData(name="Microsoft.Compute/virtualMachines/read", is_data_action=False),
            OperationData(name="Microsoft.Compute/virtualMachines/write", is_data_action=False),
            OperationData(name="Microsoft.Compute/disks/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read", is_data_action=True),
            OperationData(name="Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write", is_data_action=True),
        ]

        events = [
            self._make_event(
                "created",
                self._make_role_json(
                    actions=["Microsoft.Compute/*/read"],  # 1 pattern → 2 effective (VMs + disks)
                    data_actions=["Microsoft.Storage/storageAccounts/blobServices/*/read"],  # 1 pattern → 1 effective
                ),
            ),
        ]

        result = build_permission_timeline(events, all_operations=ops)
        assert result[0].actions == 1  # pattern count unchanged
        assert result[0].effective_actions == 2  # Wildcard expanded: VMs/read + disks/read
        assert result[0].data_actions == 1  # pattern count unchanged
        assert result[0].effective_data_actions == 1  # blobs/read matches
        assert result[0].effective_total == 3

    def test_effective_counts_with_not_actions(self):
        """NotActions should subtract from effective counts."""
        from azurerbac.azure.models import OperationData
        from azurerbac.web.services.pages import build_permission_timeline

        ops = [
            OperationData(name="Microsoft.Compute/virtualMachines/read", is_data_action=False),
            OperationData(name="Microsoft.Compute/virtualMachines/write", is_data_action=False),
            OperationData(name="Microsoft.Compute/virtualMachines/delete", is_data_action=False),
        ]

        role_json = {
            "id": "test-id",
            "name": "test-guid",
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {
                "roleName": "Test Role",
                "permissions": [
                    {
                        "actions": ["Microsoft.Compute/virtualMachines/*"],  # 1 pattern → 3 ops
                        "notActions": ["Microsoft.Compute/virtualMachines/delete"],  # Subtract 1
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
                "assignableScopes": ["/"],
            },
        }

        events = [self._make_event("created", role_json)]

        result = build_permission_timeline(events, all_operations=ops)
        assert result[0].actions == 1  # 1 pattern
        assert result[0].effective_actions == 2  # 3 expanded - 1 notActions = 2

    def test_delete_event_has_zero_effective_counts(self):
        """Delete events should also have zero effective counts."""
        from azurerbac.web.services.pages import build_permission_timeline

        events = [
            self._make_event(
                "deleted",
                role_json=None,
                scan_timestamp="2025-06-01T00:00:00+00:00",
            ),
        ]

        result = build_permission_timeline(events)
        assert len(result) == 1
        assert result[0].effective_actions == 0
        assert result[0].effective_data_actions == 0
        assert result[0].effective_total == 0
