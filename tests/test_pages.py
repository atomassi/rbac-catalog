"""Tests for the page-related service logic.

Keep these tests focused on meaningful behavior (permissions computation, role lookup,
provider extraction). Route wiring and template rendering are covered by E2E.
"""

from unittest.mock import MagicMock

import pytest

from azurerbac.azure.models import OperationData, RoleDefinition


def make_role(
    role_id: str = "test-role-id",
    actions: list[str] | None = None,
    not_actions: list[str] | None = None,
    data_actions: list[str] | None = None,
    not_data_actions: list[str] | None = None,
    condition: str | None = None,
) -> RoleDefinition:
    """Create a RoleDefinition for testing."""
    return RoleDefinition.model_validate(
        {
            "name": role_id,
            "properties": {
                "permissions": [
                    {
                        "actions": actions or [],
                        "notActions": not_actions or [],
                        "dataActions": data_actions or [],
                        "notDataActions": not_data_actions or [],
                        **({"condition": condition} if condition else {}),
                    }
                ]
            },
        }
    )


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

        role = make_role(actions=["Microsoft.Storage/*/read"])

        all_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
        ]

        # Create mock app_cache with pre-computed coverage
        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Storage/storageAccounts/read"},
            set(),
        )

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 1
        assert result["data_plane_count"] == 0
        assert "Microsoft.Storage/storageAccounts/read" in result["control_plane_actions"]

    def test_compute_with_not_actions(self):
        """Test that notActions are properly excluded."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(
            actions=["Microsoft.Storage/*"],
            not_actions=["Microsoft.Storage/storageAccounts/delete"],
        )

        all_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/delete", "is_data_action": False},
        ]

        # Create mock with delete excluded
        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            {
                "Microsoft.Storage/storageAccounts/read",
                "Microsoft.Storage/storageAccounts/write",
            },
            set(),
        )

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 2
        assert "Microsoft.Storage/storageAccounts/delete" not in result["control_plane_actions"]

    def test_compute_data_actions(self):
        """Test computing data plane actions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(
            data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"]
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
            {"Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"},
        )

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 0
        assert result["data_plane_count"] == 1

    def test_detect_conditions(self):
        """Test detection of conditions in permissions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(
            actions=["Microsoft.Storage/*/read"],
            condition="@Resource[Microsoft.Storage/storageAccounts:name] == 'example'",
        )

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role, [], mock_app_cache)

        assert result["has_conditions"] is True

    def test_detect_wildcards(self):
        """Test detection of wildcard patterns."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(actions=["Microsoft.Storage/*/read"])

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role, [], mock_app_cache)

        assert result["has_wildcards"] is True
        assert result["raw_actions"] == ["Microsoft.Storage/*/read"]

    def test_detect_unresolved_permissions(self):
        """Test detection of unresolved permissions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(actions=["Microsoft.OldService/*/read"])

        mock_app_cache = MagicMock()
        # Cache returns empty sets (no matching operations)
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role, [], mock_app_cache)

        # Role has actions defined but none resolved
        assert result["has_unresolved_permissions"] is True

    def test_no_unresolved_when_actions_resolve(self):
        """Test that has_unresolved_permissions is False when actions resolve."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(actions=["Microsoft.Storage/*/read"])

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Storage/storageAccounts/read"},
            set(),
        )

        result = compute_role_effective_permissions(role, [], mock_app_cache)

        assert result["has_unresolved_permissions"] is False

    def test_fallback_to_manual_computation(self):
        """Test fallback to manual computation when cache misses."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role = make_role(actions=["Microsoft.Storage/storageAccounts/read"])

        all_operations = [
            OperationData(name="Microsoft.Storage/storageAccounts/read", is_data_action=False),
            OperationData(name="Microsoft.Storage/storageAccounts/write", is_data_action=False),
        ]

        mock_app_cache = MagicMock()
        # Cache miss
        mock_app_cache.get_role_coverage.return_value = None

        result = compute_role_effective_permissions(role, all_operations, mock_app_cache)

        # Should compute manually
        assert result["control_plane_count"] == 1
        assert "Microsoft.Storage/storageAccounts/read" in result["control_plane_actions"]


class TestGetRolesAllowingOperationServices:
    """Tests for get_roles_allowing_operation function in services module."""

    def test_returns_roles_from_cache(self):
        """Test returning cached results."""
        from azurerbac.web.services.pages import get_roles_allowing_operation

        mock_app_cache = MagicMock()
        cached_roles = [{"role_id": "role1", "role_name": "Storage Reader"}]
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
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Storage/storageAccounts/read"},
            set(),
        )

        result = get_roles_allowing_operation(
            "Microsoft.Storage/storageAccounts/read", False, mock_app_cache
        )

        assert len(result) == 1
        assert result[0]["role_name"] == "Storage Reader"
        assert result[0]["role_id"] == "role1"

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


class TestGetUniqueProviders:
    """Tests for get_unique_providers function."""

    @pytest.mark.asyncio
    async def test_returns_cached_providers(self):
        """Test returning cached providers."""
        from azurerbac.web.services.pages import get_unique_providers

        app_cache = MagicMock()
        app_cache.cache.unique_providers = ["Microsoft.Compute", "Microsoft.Storage"]

        async def mock_get_all_operations():
            return []

        result = await get_unique_providers(app_cache, mock_get_all_operations)

        assert result == ["Microsoft.Compute", "Microsoft.Storage"]

    @pytest.mark.asyncio
    async def test_computes_providers_when_not_cached(self):
        """Test computing providers when not cached."""
        from azurerbac.azure.models import OperationData
        from azurerbac.web.services.pages import get_unique_providers

        app_cache = MagicMock()
        app_cache.cache.unique_providers = None

        async def mock_get_all_operations():
            return [
                OperationData(name="op1", provider_display_name="Microsoft.Compute"),
                OperationData(name="op2", provider_display_name="Microsoft.Storage"),
                OperationData(name="op3", provider_display_name="Microsoft.Compute"),  # Duplicate
            ]

        result = await get_unique_providers(app_cache, mock_get_all_operations)

        assert result == ["Microsoft.Compute", "Microsoft.Storage"]
        app_cache.set_metadata.assert_called_once()
        call_kwargs = app_cache.set_metadata.call_args[1]
        assert "unique_providers" in call_kwargs


class TestEnrichEventWithDiff:
    """Tests for enrich_event_with_diff function."""

    def test_enriches_event_with_role_json_pretty(self):
        """Test that role_json is formatted as role_json_pretty."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = {
            "scan_timestamp": "2025-01-01T00:00:00Z",
            "azure_updated_on": "2025-01-01T00:00:00Z",
            "event_type": "created",
            "summary": "Role created",
            "diff_json": {"changed": True, "changes": []},
            "role_json": {
                "id": "test-id",
                "name": "test-guid",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {"roleName": "Test Role", "type": "BuiltInRole"},
            },
        }

        result = enrich_event_with_diff(event)

        assert "role_json_pretty" in result
        assert '"roleName": "Test Role"' in result["role_json_pretty"]
        assert '"type": "BuiltInRole"' in result["role_json_pretty"]

    def test_role_json_pretty_empty_when_no_role_json(self):
        """Test that role_json_pretty is empty for deleted events."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = {
            "scan_timestamp": "2025-01-01T00:00:00Z",
            "azure_updated_on": "2025-01-01T00:00:00Z",
            "event_type": "deleted",
            "summary": "Role deleted",
            "diff_json": {"changed": True, "changes": []},
            "role_json": None,  # NULL for deleted
        }

        result = enrich_event_with_diff(event)

        assert result["role_json_pretty"] == ""

    def test_applies_sanitization_to_role_json(self):
        """Test that role_json is sanitized (isServiceRole excluded via RoleDefinition model)."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = {
            "scan_timestamp": "2025-01-01T00:00:00Z",
            "event_type": "created",
            "summary": "Created",
            "diff_json": {},
            "role_json": {
                "id": "test",
                "name": "test-guid",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {"roleName": "Test", "isServiceRole": True},
            },
        }

        result = enrich_event_with_diff(event)

        # isServiceRole is excluded by RoleDefinition.to_dict()
        assert "isServiceRole" not in result["role_json_pretty"]
        assert "roleName" in result["role_json_pretty"]

    def test_diff_json_is_processed(self):
        """Test that diff_json is included in result."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = {
            "scan_timestamp": "2025-01-01T00:00:00Z",
            "event_type": "updated",
            "summary": "Updated",
            "diff_json": {
                "changed": True,
                "changes": [{"path": "description", "from": "old", "to": "new"}],
            },
            "role_json": None,
        }

        result = enrich_event_with_diff(event)

        assert result["diff"] is not None
        assert result["diff"]["changed"] is True
        assert len(result["diff"]["changes"]) == 1
