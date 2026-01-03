"""Tests for the page-related service logic.

Keep these tests focused on meaningful behavior (permissions computation, role lookup,
provider extraction). Route wiring and template rendering are covered by E2E.
"""

from unittest.mock import MagicMock

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

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

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

        result = compute_role_effective_permissions(role_json, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 1
        assert result["data_plane_count"] == 0
        assert "Microsoft.Storage/storageAccounts/read" in result["control_plane_actions"]

    def test_compute_with_not_actions(self):
        """Test that notActions are properly excluded."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*"],
                        "notActions": ["Microsoft.Storage/storageAccounts/delete"],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

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

        result = compute_role_effective_permissions(role_json, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 2
        assert "Microsoft.Storage/storageAccounts/delete" not in result["control_plane_actions"]

    def test_compute_data_actions(self):
        """Test computing data plane actions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": [],
                        "notActions": [],
                        "dataActions": [
                            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
                        ],
                        "notDataActions": [],
                    }
                ]
            },
        }

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

        result = compute_role_effective_permissions(role_json, all_operations, mock_app_cache)

        assert result["control_plane_count"] == 0
        assert result["data_plane_count"] == 1

    def test_detect_conditions(self):
        """Test detection of conditions in permissions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                        "condition": (
                            "@Resource[Microsoft.Storage/storageAccounts:name] == 'example'"
                        ),
                    }
                ]
            },
        }

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role_json, [], mock_app_cache)

        assert result["has_conditions"] is True

    def test_detect_wildcards(self):
        """Test detection of wildcard patterns."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role_json, [], mock_app_cache)

        assert result["has_wildcards"] is True
        assert result["raw_actions"] == ["Microsoft.Storage/*/read"]

    def test_detect_unresolved_permissions(self):
        """Test detection of unresolved permissions."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.OldService/*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

        mock_app_cache = MagicMock()
        # Cache returns empty sets (no matching operations)
        mock_app_cache.get_role_coverage.return_value = (set(), set())

        result = compute_role_effective_permissions(role_json, [], mock_app_cache)

        # Role has actions defined but none resolved
        assert result["has_unresolved_permissions"] is True

    def test_no_unresolved_when_actions_resolve(self):
        """Test that has_unresolved_permissions is False when actions resolve."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_coverage.return_value = (
            {"Microsoft.Storage/storageAccounts/read"},
            set(),
        )

        result = compute_role_effective_permissions(role_json, [], mock_app_cache)

        assert result["has_unresolved_permissions"] is False

    def test_fallback_to_manual_computation(self):
        """Test fallback to manual computation when cache misses."""
        from azurerbac.web.services.pages import compute_role_effective_permissions

        role_json = {
            "name": "test-role-id",
            "properties": {
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/storageAccounts/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ]
            },
        }

        all_operations = [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Storage/storageAccounts/write", "is_data_action": False},
        ]

        mock_app_cache = MagicMock()
        # Cache miss
        mock_app_cache.get_role_coverage.return_value = None

        result = compute_role_effective_permissions(role_json, all_operations, mock_app_cache)

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

        # Mock role data
        role_jsons = [
            {
                "name": "role1",
                "properties": {
                    "roleName": "Storage Reader",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["Microsoft.Storage/storageAccounts/read"]}],
                },
            }
        ]
        mock_app_cache.get_all_role_jsons.return_value = role_jsons
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

        role_jsons = [
            {
                "name": "role1",
                "properties": {
                    "roleName": "Compute Reader",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["Microsoft.Compute/*/read"]}],
                },
            }
        ]
        mock_app_cache.get_all_role_jsons.return_value = role_jsons
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
        role_jsons = [
            {
                "name": "role1",
                "properties": {
                    "roleName": "Test Role",
                    "type": "BuiltInRole",
                    "permissions": [{"actions": ["*"]}],
                },
            }
        ]
        mock_app_cache.get_all_role_jsons.return_value = role_jsons
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

    def test_returns_cached_providers(self):
        """Test returning cached providers."""
        import asyncio

        from azurerbac.web.services.pages import get_unique_providers

        app_cache = MagicMock()
        app_cache.cache.unique_providers = ["Microsoft.Compute", "Microsoft.Storage"]

        async def mock_get_all_operations():
            return []

        result = asyncio.get_event_loop().run_until_complete(
            get_unique_providers(app_cache, mock_get_all_operations)
        )

        assert result == ["Microsoft.Compute", "Microsoft.Storage"]

    def test_computes_providers_when_not_cached(self):
        """Test computing providers when not cached."""
        import asyncio

        from azurerbac.web.services.pages import get_unique_providers

        app_cache = MagicMock()
        app_cache.cache.unique_providers = None

        async def mock_get_all_operations():
            return [
                {"name": "op1", "provider_display_name": "Microsoft.Compute"},
                {"name": "op2", "provider_display_name": "Microsoft.Storage"},
                {"name": "op3", "provider_display_name": "Microsoft.Compute"},  # Duplicate
            ]

        result = asyncio.get_event_loop().run_until_complete(
            get_unique_providers(app_cache, mock_get_all_operations)
        )

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
                "properties": {"roleName": "Test Role", "type": "BuiltInRole"},
            },
        }

        def identity(x):
            return x

        result = enrich_event_with_diff(event, identity)

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

        def identity(x):
            return x

        result = enrich_event_with_diff(event, identity)

        assert result["role_json_pretty"] == ""

    def test_applies_sanitization_to_role_json(self):
        """Test that role_json is sanitized before formatting."""
        from azurerbac.web.services.pages import enrich_event_with_diff

        event = {
            "scan_timestamp": "2025-01-01T00:00:00Z",
            "event_type": "created",
            "summary": "Created",
            "diff_json": {},
            "role_json": {
                "id": "test",
                "properties": {"roleName": "Test", "isServiceRole": True},
            },
        }

        def remove_service_role(j):
            """Simulates removing isServiceRole field."""
            result = dict(j)
            if "properties" in result:
                props = dict(result["properties"])
                props.pop("isServiceRole", None)
                result["properties"] = props
            return result

        result = enrich_event_with_diff(event, remove_service_role)

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

        def identity(x):
            return x

        result = enrich_event_with_diff(event, identity)

        assert result["diff"] is not None
        assert result["diff"]["changed"] is True
        assert len(result["diff"]["changes"]) == 1
