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

    def test_both_empty(self):
        from azurerbac.web.services.pages import _jaccard

        assert _jaccard(frozenset(), frozenset()) == 1.0

    def test_identical_sets(self):
        from azurerbac.web.services.pages import _jaccard

        s = frozenset({"a", "b", "c"})
        assert _jaccard(s, s) == 1.0

    def test_disjoint_sets(self):
        from azurerbac.web.services.pages import _jaccard

        assert _jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0

    def test_partial_overlap(self):
        from azurerbac.web.services.pages import _jaccard

        a = frozenset({"a", "b", "c"})
        b = frozenset({"b", "c", "d"})
        # intersection=2, union=4 → 0.5
        assert _jaccard(a, b) == pytest.approx(0.5)

    def test_one_empty(self):
        from azurerbac.web.services.pages import _jaccard

        assert _jaccard(frozenset({"a"}), frozenset()) == 0.0

    def test_subset(self):
        from azurerbac.web.services.pages import _jaccard

        a = frozenset({"a", "b"})
        b = frozenset({"a", "b", "c"})
        # intersection=2, union=3
        assert _jaccard(a, b) == pytest.approx(2 / 3)


class TestConditionSimilarity:
    """Tests for _condition_similarity function."""

    def test_both_empty_returns_one(self):
        from azurerbac.web.services.pages import _condition_similarity

        assert _condition_similarity(frozenset(), frozenset()) == 1.0

    def test_first_empty_second_not_returns_zero(self):
        from azurerbac.web.services.pages import _condition_similarity

        assert _condition_similarity(frozenset(), frozenset({"cond1"})) == 0.0

    def test_first_not_empty_second_empty_returns_zero(self):
        from azurerbac.web.services.pages import _condition_similarity

        assert _condition_similarity(frozenset({"cond1"}), frozenset()) == 0.0

    def test_identical_conditions(self):
        from azurerbac.web.services.pages import _condition_similarity

        c = frozenset({"@Resource[Microsoft.Storage/storageAccounts/blobServices]"})
        assert _condition_similarity(c, c) == 1.0

    def test_partial_overlap_conditions(self):
        from azurerbac.web.services.pages import _condition_similarity

        a = frozenset({"condA", "condB"})
        b = frozenset({"condB", "condC"})
        # Jaccard: intersection=1, union=3
        assert _condition_similarity(a, b) == pytest.approx(1 / 3)


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
    mock.cache.operation_to_roles = op_to_roles
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

        # First call computes and caches
        first = compute_related_roles("r1", cache=cache)
        assert len(first) == 1

        # Second call should hit cache — verify via get_related_roles
        cache.get_related_roles.assert_called_with("r1")
        second = compute_related_roles("r1", cache=cache)
        assert second == first

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
