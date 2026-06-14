"""Tests for the Azure API modules (operations and roles)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from rbaccatalog.azure.models import OperationData, Permission, RoleDefinition, RoleProperties


def _transform_role(item: dict[str, Any]) -> dict[str, Any]:
    """Test helper: transform an RBAC API role payload to canonical dict."""
    return RoleDefinition.from_rbac_api(item).to_dict()


class TestFlattenProviderOperationsPayload:
    def test_flattens_provider_and_resource_type_operations(self):
        from rbaccatalog.azure.operations import _flatten_provider_operations

        payload = {
            "value": [
                {
                    "displayName": "Microsoft Compute",
                    "operations": [
                        {
                            "name": "Microsoft.Compute/register/action",
                            "displayName": "Register Subscription",
                            "description": "Registers the subscription",
                            "origin": "user",
                            "isDataAction": False,
                        }
                    ],
                    "resourceTypes": [
                        {
                            "name": "virtualMachines",
                            "displayName": "Virtual Machines",
                            "operations": [
                                {
                                    "name": "Microsoft.Compute/virtualMachines/read",
                                    "displayName": "Get Virtual Machine",
                                    "isDataAction": False,
                                },
                                {
                                    "name": "Microsoft.Compute/virtualMachines/write",
                                    "displayName": "Create/Update VM",
                                    "isDataAction": False,
                                },
                            ],
                        }
                    ],
                }
            ]
        }

        operations = _flatten_provider_operations(payload)
        assert len(operations) == 3

        provider_op = operations[0]
        assert provider_op.name == "Microsoft.Compute/register/action"
        assert provider_op.provider_display_name == "Microsoft Compute"
        assert provider_op.resource_type is None
        assert provider_op.resource_type_display_name is None
        assert provider_op.is_data_action is False

        rt_ops = operations[1:]
        assert all(op.resource_type == "virtualMachines" for op in rt_ops)
        assert all(op.resource_type_display_name == "Virtual Machines" for op in rt_ops)

    def test_handles_empty_payload(self):
        from rbaccatalog.azure.operations import _flatten_provider_operations

        assert _flatten_provider_operations({"value": []}) == []

    def test_defaults_missing_sections(self):
        from rbaccatalog.azure.operations import _flatten_provider_operations

        payload = {"value": [{"displayName": "X"}]}
        operations = _flatten_provider_operations(payload)
        assert operations == []


# =============================================================================
# Azure Roles Tests
# =============================================================================


def _make_input_item(
    role_id: str = "/providers/Microsoft.Authorization/roleDefinitions/test-guid",
    role_name: str = "Test Role",
    role_type: str = "BuiltInRole",
    permissions: list | None = None,
    **extra_props,
) -> dict:
    """Helper to create RBAC API-shaped input items for transformation."""
    props = {
        "roleName": role_name,
        "type": role_type,
        "permissions": permissions or [],
        **extra_props,
    }
    # RBAC API returns ``name`` (the GUID) as a top-level field; derive it
    # from the id when the caller doesn't override it.
    name = role_id.rsplit("/", 1)[-1] if "/" in role_id else role_id
    return {"id": role_id, "name": name, "properties": props}


# =============================================================================
# Tests for Permission Field Normalization
# =============================================================================


class TestPermissionNormalization:
    """Tests for permission field case normalization."""

    @pytest.mark.parametrize(
        "input_key,expected_key,input_value",
        [
            ("actions", "actions", ["Microsoft.Compute/*/read"]),
            ("Actions", "actions", ["Microsoft.Compute/*/read"]),
            ("notActions", "notActions", ["Microsoft.Authorization/*"]),
            ("NotActions", "notActions", ["Microsoft.Authorization/*"]),
            ("dataActions", "dataActions", ["Microsoft.Storage/*/blob/read"]),
            ("DataActions", "dataActions", ["Microsoft.Storage/*/blob/read"]),
            ("notDataActions", "notDataActions", ["Microsoft.Storage/*/blob/delete"]),
            ("NotDataActions", "notDataActions", ["Microsoft.Storage/*/blob/delete"]),
        ],
    )
    def test_permission_key_normalization(
        self, input_key: str, expected_key: str, input_value: list
    ):
        """Test that permission keys are normalized correctly."""
        item = _make_input_item(permissions=[{input_key: input_value}])
        result = _transform_role(item)
        perms = result["properties"]["permissions"][0]
        assert expected_key in perms
        assert perms[expected_key] == input_value

    def test_handles_missing_permission_fields(self):
        """Test that missing permission fields default to empty lists."""
        item = _make_input_item(
            permissions=[{"actions": ["Microsoft.Compute/virtualMachines/read"]}]
        )
        result = _transform_role(item)
        perms = result["properties"]["permissions"][0]
        assert perms["actions"] == ["Microsoft.Compute/virtualMachines/read"]
        assert perms["notActions"] == []
        assert perms["dataActions"] == []
        assert perms["notDataActions"] == []

    def test_handles_null_permission_values(self):
        """Test handling of null values in permissions."""
        item = _make_input_item(
            permissions=[
                {"actions": None, "notActions": None, "dataActions": None, "notDataActions": None}
            ]
        )
        result = _transform_role(item)
        perms = result["properties"]["permissions"][0]
        assert perms["actions"] == []
        assert perms["notActions"] == []
        assert perms["dataActions"] == []
        assert perms["notDataActions"] == []

    def test_preserves_condition_fields(self):
        """Test that condition and conditionVersion are normalized to lowercase."""
        item = _make_input_item(
            permissions=[
                {
                    "actions": ["*"],
                    "Condition": "@Resource[Microsoft.Storage/storageAccounts:name] == 'test'",
                    "ConditionVersion": "2.0",
                }
            ]
        )
        result = _transform_role(item)
        perms = result["properties"]["permissions"][0]
        assert perms["condition"] == "@Resource[Microsoft.Storage/storageAccounts:name] == 'test'"
        assert perms["conditionVersion"] == "2.0"


# =============================================================================
# Tests for Full Transformation
# =============================================================================


class TestFullTransformation:
    """Tests for complete role transformation."""

    def test_preserves_all_expected_fields(self):
        """Test that all expected fields are in the output."""
        item = _make_input_item(
            role_name="Full Role",
            role_type="BuiltInRole",
            description="Full description",
            assignableScopes=["/", "/subscriptions/123"],
            createdOn="2020-01-01T00:00:00Z",
            updatedOn="2021-01-01T00:00:00Z",
            createdBy="user1",
            updatedBy="user2",
            isServiceRole=False,
        )
        result = _transform_role(item)

        props = result["properties"]
        assert props["roleName"] == "Full Role"
        assert props["type"] == "BuiltInRole"
        assert props["description"] == "Full description"
        assert props["assignableScopes"] == ["/", "/subscriptions/123"]
        # Compare as datetime since format may vary (Z vs +00:00)
        from datetime import datetime

        assert datetime.fromisoformat(props["createdOn"]) == datetime.fromisoformat(
            "2020-01-01T00:00:00Z"
        )
        assert datetime.fromisoformat(props["updatedOn"]) == datetime.fromisoformat(
            "2021-01-01T00:00:00Z"
        )
        assert props["createdBy"] == "user1"
        assert props["updatedBy"] == "user2"

    def test_handles_empty_properties(self):
        """Test handling of empty properties object."""
        item = {
            "id": "/providers/Microsoft.Authorization/roleDefinitions/empty",
            "name": "empty",
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {},
        }
        result = _transform_role(item)
        assert result["properties"]["roleName"] == ""
        assert result["properties"]["type"] == ""
        assert result["properties"]["permissions"] == []

    def test_handles_multiple_permissions(self):
        """Test handling of multiple permission objects."""
        item = _make_input_item(
            permissions=[
                {"actions": ["action1"]},
                {"actions": ["action2"], "dataActions": ["dataAction1"]},
                {"notActions": ["notAction1"]},
            ]
        )
        result = _transform_role(item)
        perms = result["properties"]["permissions"]
        assert len(perms) == 3
        assert perms[0]["actions"] == ["action1"]
        assert perms[1]["actions"] == ["action2"]
        assert perms[1]["dataActions"] == ["dataAction1"]
        assert perms[2]["notActions"] == ["notAction1"]


# =============================================================================
# Tests for Module Structure
# =============================================================================


class TestRoles:
    def test_transform_smoke(self):
        item = _make_input_item()
        result = _transform_role(item)
        assert result["type"] == "Microsoft.Authorization/roleDefinitions"


# =============================================================================
# Tests for RoleDefinition DateTime Parsing
# =============================================================================


class TestRoleDefinitionDateTimeParsing:
    """Test datetime parsing for createdOn/updatedOn fields."""

    def test_valid_iso_datetime_parsed(self):
        """Valid ISO datetime strings are parsed to datetime objects."""
        role = RoleDefinition.model_validate(
            {
                "id": "/providers/Microsoft.Authorization/roleDefinitions/test",
                "name": "test",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {
                    "roleName": "Test",
                    "type": "BuiltInRole",
                    "description": "Test",
                    "assignableScopes": ["/"],
                    "permissions": [],
                    "createdOn": "2024-01-15T10:30:00.0000000Z",
                    "updatedOn": "2024-06-20T15:45:30.1234567Z",
                },
            }
        )
        assert role.properties.created_on is not None
        assert role.properties.created_on.year == 2024
        assert role.properties.created_on.month == 1
        assert role.properties.created_on.day == 15
        assert role.properties.updated_on is not None
        assert role.properties.updated_on.year == 2024
        assert role.properties.updated_on.month == 6

    @pytest.mark.parametrize(
        ("created_on", "updated_on"),
        [
            pytest.param("", "", id="empty_string"),
            pytest.param("not-a-date", "invalid-timestamp", id="invalid_string"),
            pytest.param(None, None, id="null_value"),
        ],
    )
    def test_invalid_or_empty_datetime_becomes_none(
        self, created_on: str | None, updated_on: str | None
    ):
        """Empty, invalid, or null datetime values become None."""
        role = RoleDefinition.model_validate(
            {
                "id": "/providers/Microsoft.Authorization/roleDefinitions/test",
                "name": "test",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {
                    "roleName": "Test",
                    "type": "BuiltInRole",
                    "description": "Test",
                    "assignableScopes": ["/"],
                    "permissions": [],
                    "createdOn": created_on,
                    "updatedOn": updated_on,
                },
            }
        )
        assert role.properties.created_on is None
        assert role.properties.updated_on is None

    def test_missing_fields_default_to_none(self):
        """Missing datetime fields default to None."""
        role = RoleDefinition.model_validate(
            {
                "id": "/providers/Microsoft.Authorization/roleDefinitions/test",
                "name": "test",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {
                    "roleName": "Test",
                    "type": "BuiltInRole",
                    "description": "Test",
                    "assignableScopes": ["/"],
                    "permissions": [],
                },
            }
        )
        assert role.properties.created_on is None
        assert role.properties.updated_on is None


# =============================================================================
# Tests for Case-Insensitive Parsing
# =============================================================================


class TestRolePropertiesCaseInsensitive:
    """Tests for RoleProperties case-insensitive field parsing."""

    @pytest.mark.parametrize(
        "key_case",
        [
            # camelCase (standard)
            {"roleName": "Test", "assignableScopes": ["/"], "createdOn": "2024-01-01T00:00:00Z"},
            # PascalCase
            {"RoleName": "Test", "AssignableScopes": ["/"], "CreatedOn": "2024-01-01T00:00:00Z"},
            # UPPERCASE
            {"ROLENAME": "Test", "ASSIGNABLESCOPES": ["/"], "CREATEDON": "2024-01-01T00:00:00Z"},
            # lowercase
            {"rolename": "Test", "assignablescopes": ["/"], "createdon": "2024-01-01T00:00:00Z"},
            # snake_case (Python-style)
            {"role_name": "Test", "assignable_scopes": ["/"], "created_on": "2024-01-01T00:00:00Z"},
        ],
    )
    def test_role_properties_parses_any_case(self, key_case: dict):
        """Test that RoleProperties accepts field names in any case."""
        from rbaccatalog.azure.models import RoleProperties

        props = RoleProperties.model_validate(key_case)
        assert props.role_name == "Test"
        assert props.assignable_scopes == ["/"]
        assert props.created_on is not None

    def test_role_properties_preserves_unknown_fields(self):
        """Test that unknown fields pass through unchanged."""
        from rbaccatalog.azure.models import RoleProperties

        # Unknown fields should be ignored by default Pydantic config
        props = RoleProperties.model_validate(
            {
                "roleName": "Test",
                "unknownField": "value",
            }
        )
        assert props.role_name == "Test"


class TestRoleDefinitionCaseInsensitive:
    """Tests for RoleDefinition case-insensitive field parsing."""

    @pytest.mark.parametrize(
        "key_case",
        [
            # camelCase (standard)
            {
                "id": "/test",
                "name": "test-guid",
                "type": "Test",
                "properties": {"roleName": "Test"},
            },
            # PascalCase
            {
                "Id": "/test",
                "Name": "test-guid",
                "Type": "Test",
                "Properties": {"roleName": "Test"},
            },
            # UPPERCASE
            {
                "ID": "/test",
                "NAME": "test-guid",
                "TYPE": "Test",
                "PROPERTIES": {"roleName": "Test"},
            },
            # lowercase
            {
                "id": "/test",
                "name": "test-guid",
                "type": "Test",
                "properties": {"roleName": "Test"},
            },
        ],
    )
    def test_role_definition_parses_any_case(self, key_case: dict):
        """Test that RoleDefinition accepts field names in any case."""
        from rbaccatalog.azure.models import RoleDefinition

        role = RoleDefinition.model_validate(key_case)
        assert role.id == "/test"
        assert role.name == "test-guid"
        assert role.properties.role_name == "Test"

    def test_role_definition_with_nested_properties_case(self):
        """Test that nested RoleProperties also handles case variations."""
        from rbaccatalog.azure.models import RoleDefinition

        # PascalCase outer, camelCase inner
        role = RoleDefinition.model_validate(
            {
                "Id": "/test",
                "Name": "guid",
                "Properties": {
                    "RoleName": "Nested Test",
                    "AssignableScopes": ["/subscriptions/123"],
                    "Permissions": [
                        {"Actions": ["*"], "NotActions": ["Microsoft.Authorization/*"]}
                    ],
                },
            }
        )
        assert role.properties.role_name == "Nested Test"
        assert role.properties.assignable_scopes == ["/subscriptions/123"]
        assert len(role.properties.permissions) == 1
        assert role.properties.permissions[0].actions == ["*"]
        assert role.properties.permissions[0].not_actions == ["Microsoft.Authorization/*"]


# =============================================================================
# RoleDefinition Parsing Edge Cases
# =============================================================================


class TestRoleDefinitionEdgeCases:
    """Edge cases for RoleDefinition parsing that could cause runtime errors."""

    def test_properties_is_none_raises_validation_error(self):
        """RoleDefinition raises ValidationError when properties is null."""
        # Pydantic does NOT auto-convert None to default for nested models
        with pytest.raises(ValidationError, match="properties"):
            RoleDefinition.model_validate(
                {
                    "id": "/test",
                    "name": "test-guid",
                    "properties": None,
                }
            )

    def test_properties_key_missing_uses_defaults(self):
        """RoleDefinition handles missing properties key with defaults."""
        role = RoleDefinition.model_validate(
            {
                "id": "/test",
                "name": "test-guid",
            }
        )
        assert role.properties.role_name == ""
        assert role.properties.permissions == []

    def test_non_dict_input_raises_validation_error(self):
        """model_validate with non-dict raises ValidationError."""
        with pytest.raises(ValidationError):
            RoleDefinition.model_validate("not a dict")

        with pytest.raises(ValidationError):
            RoleDefinition.model_validate(["list", "of", "items"])

        with pytest.raises(ValidationError):
            RoleDefinition.model_validate(12345)

    def test_minimal_role_with_just_name(self):
        """RoleDefinition handles minimal input with just name."""
        role = RoleDefinition.model_validate({"name": "minimal-guid"})
        assert role.name == "minimal-guid"
        assert role.role_id == "minimal-guid"
        assert role.id == ""
        assert role.properties.role_name == ""

    def test_empty_dict_uses_all_defaults(self):
        """RoleDefinition handles empty dict with all defaults."""
        role = RoleDefinition.model_validate({})
        assert role.id == ""
        assert role.name == ""
        assert role.type == "Microsoft.Authorization/roleDefinitions"
        assert role.properties.role_name == ""

    def test_deeply_nested_permissions_many_actions(self):
        """RoleDefinition handles 100+ actions in permission block."""
        many_actions = [f"Microsoft.Test/resource{i}/read" for i in range(150)]
        role = RoleDefinition.model_validate(
            {
                "name": "many-actions",
                "properties": {
                    "roleName": "Many Actions Role",
                    "permissions": [{"actions": many_actions}],
                },
            }
        )
        assert len(role.properties.permissions[0].actions) == 150

    def test_condition_with_special_characters_preserved(self):
        """ABAC conditions with special chars (@, [], quotes) are preserved."""
        complex_condition = (
            "@Resource[Microsoft.Storage/storageAccounts/blobServices/containers:name] "
            "StringEquals 'my-container' AND @Principal[microsoft.directory/users:mail] "
            "StringLike '*@contoso.com'"
        )
        role = RoleDefinition.model_validate(
            {
                "name": "condition-test",
                "properties": {
                    "roleName": "Condition Test",
                    "permissions": [
                        {
                            "actions": ["*"],
                            "condition": complex_condition,
                            "conditionVersion": "2.0",
                        }
                    ],
                },
            }
        )
        assert role.properties.permissions[0].condition == complex_condition

    @pytest.mark.parametrize(
        ("condition", "condition_version", "expected_has_condition"),
        [
            pytest.param("", None, False, id="empty_string"),
            pytest.param(None, None, False, id="none"),
            pytest.param(None, "2.0", False, id="version_only"),
        ],
    )
    def test_condition_falsy_values(self, condition, condition_version, expected_has_condition):
        """Falsy condition values mean has_condition is False."""
        perm_data = {"actions": ["*"], "condition": condition}
        if condition_version:
            perm_data["conditionVersion"] = condition_version
        perm = Permission.model_validate(perm_data)
        assert perm.has_condition == expected_has_condition


class TestPermissionEdgeCases:
    """Edge cases for Permission model."""

    def test_all_action_lists_empty(self):
        """Permission with all action lists empty is valid."""
        perm = Permission.model_validate({})
        assert perm.actions == []
        assert perm.data_actions == []
        assert perm.not_actions == []
        assert perm.not_data_actions == []

    def test_null_values_become_empty_lists(self):
        """Null values for action lists become empty lists."""
        perm = Permission.model_validate(
            {
                "actions": None,
                "dataActions": None,
                "notActions": None,
                "notDataActions": None,
            }
        )
        assert perm.actions == []
        assert perm.data_actions == []
        assert perm.not_actions == []
        assert perm.not_data_actions == []

    def test_to_dict_preserves_order(self):
        """to_dict output has consistent key ordering matching Azure API."""
        perm = Permission.model_validate(
            {
                "actions": ["b", "a"],
                "dataActions": ["d", "c"],
            }
        )
        result = perm.to_dict()
        keys = list(result.keys())
        # Order matches Azure API: actions, notActions, dataActions, notDataActions
        assert keys[:4] == ["actions", "notActions", "dataActions", "notDataActions"]

    def test_to_comparable_dict_sorts_actions(self):
        """to_comparable_dict sorts action lists."""
        perm = Permission.model_validate(
            {
                "actions": ["z", "a", "m"],
                "dataActions": ["3", "1", "2"],
            }
        )
        result = perm.to_comparable_dict()
        assert result["actions"] == ["a", "m", "z"]
        assert result["dataActions"] == ["1", "2", "3"]


class TestRoleDefinitionToDict:
    """Tests for RoleDefinition.to_dict() output format and field ordering."""

    @pytest.fixture
    def sample_role(self) -> RoleDefinition:
        """Create a sample RoleDefinition for testing."""
        return RoleDefinition.model_validate(
            {
                "id": "/providers/Microsoft.Authorization/roleDefinitions/test-id",
                "name": "test-id",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {
                    "roleName": "Test Role",
                    "description": "A test role description",
                    "type": "BuiltInRole",
                    "assignableScopes": ["/"],
                    "permissions": [
                        {
                            "actions": ["Microsoft.Test/read"],
                            "notActions": [],
                            "dataActions": ["Microsoft.Test/data/read"],
                            "notDataActions": [],
                        }
                    ],
                    "createdOn": "2024-01-01T00:00:00+00:00",
                    "updatedOn": "2024-06-01T00:00:00+00:00",
                    "createdBy": None,
                    "updatedBy": None,
                },
            }
        )

    def test_to_dict_top_level_field_order(self, sample_role):
        """to_dict output has correct top-level field order."""
        result = sample_role.to_dict()
        keys = list(result.keys())
        assert keys == ["id", "name", "properties", "type"]

    def test_to_dict_properties_field_order(self, sample_role):
        """to_dict properties has correct field order matching Azure API."""
        result = sample_role.to_dict()
        props_keys = list(result["properties"].keys())
        # Order must match Azure API exactly
        assert props_keys == [
            "assignableScopes",
            "createdBy",
            "createdOn",
            "description",
            "permissions",
            "roleName",
            "type",
            "updatedBy",
            "updatedOn",
        ]

    def test_to_dict_permission_field_order(self, sample_role):
        """to_dict permissions have correct field order matching Azure API."""
        result = sample_role.to_dict()
        perm = result["properties"]["permissions"][0]
        perm_keys = list(perm.keys())
        # Order must match Azure API: actions, notActions, dataActions, notDataActions
        assert perm_keys == ["actions", "notActions", "dataActions", "notDataActions"]

    def test_to_dict_preserves_values(self, sample_role):
        """to_dict correctly preserves all field values."""
        result = sample_role.to_dict()
        assert result["id"] == "/providers/Microsoft.Authorization/roleDefinitions/test-id"
        assert result["name"] == "test-id"
        assert result["type"] == "Microsoft.Authorization/roleDefinitions"

        props = result["properties"]
        assert props["roleName"] == "Test Role"
        assert props["description"] == "A test role description"
        assert props["type"] == "BuiltInRole"
        assert props["assignableScopes"] == ["/"]
        assert len(props["permissions"]) == 1
        # Timestamps should use Z suffix with milliseconds precision (Azure format)
        assert props["createdOn"] == "2024-01-01T00:00:00.000Z"
        assert props["updatedOn"] == "2024-06-01T00:00:00.000Z"

    def test_to_dict_permission_values(self, sample_role):
        """to_dict correctly preserves permission values."""
        result = sample_role.to_dict()
        perm = result["properties"]["permissions"][0]
        assert perm["actions"] == ["Microsoft.Test/read"]
        assert perm["notActions"] == []
        assert perm["dataActions"] == ["Microsoft.Test/data/read"]
        assert perm["notDataActions"] == []

    def test_to_dict_timestamps_use_z_suffix(self, sample_role):
        """to_dict timestamps use 'Z' suffix matching Azure API format."""
        result = sample_role.to_dict()
        props = result["properties"]

        # Both timestamps should use Z suffix (not +00:00)
        assert props["createdOn"].endswith("Z"), f"Expected Z suffix: {props['createdOn']}"
        assert props["updatedOn"].endswith("Z"), f"Expected Z suffix: {props['updatedOn']}"
        assert "+00:00" not in props["createdOn"]
        assert "+00:00" not in props["updatedOn"]

    def test_to_dict_timestamps_with_microseconds(self):
        """to_dict handles timestamps with microseconds correctly."""

        role = RoleDefinition.model_validate(
            {
                "id": "/providers/Microsoft.Authorization/roleDefinitions/test-id",
                "name": "test-id",
                "type": "Microsoft.Authorization/roleDefinitions",
                "properties": {
                    "roleName": "Test",
                    "type": "BuiltInRole",
                    "description": "",
                    "assignableScopes": ["/"],
                    "permissions": [],
                    "createdOn": "2025-12-17T09:58:12.949Z",
                    "updatedOn": "2025-12-17T09:58:12.949Z",
                },
            }
        )
        result = role.to_dict()
        props = result["properties"]

        # Should preserve microseconds and use Z suffix
        assert props["createdOn"] == "2025-12-17T09:58:12.949Z"
        assert props["updatedOn"] == "2025-12-17T09:58:12.949Z"


class TestRolePropertiesEdgeCases:
    """Edge cases for RoleProperties model."""

    def test_all_fields_missing(self):
        """RoleProperties handles all fields missing."""
        props = RoleProperties.model_validate({})
        assert props.role_name == ""
        assert props.type == ""
        assert props.description == ""
        assert props.assignable_scopes == []
        assert props.permissions == []

    def test_assignable_scopes_none_raises_validation_error(self):
        """assignableScopes: null raises ValidationError (list expected)."""
        # Pydantic does NOT auto-convert None to [] for list fields
        with pytest.raises(ValidationError, match="assignable_scopes"):
            RoleProperties.model_validate({"assignableScopes": None})


class TestOperationDataEdgeCases:
    """Edge cases for OperationData model."""

    def test_minimal_operation(self):
        """OperationData handles minimal input."""
        op = OperationData.model_validate({"name": "Microsoft.Test/read"})
        assert op.name == "Microsoft.Test/read"
        assert op.display_name is None
        assert op.description is None
        assert op.is_data_action is False

    def test_empty_dict(self):
        """OperationData handles empty dict."""
        op = OperationData.model_validate({})
        assert op.name == ""

    def test_from_azure_with_missing_fields(self):
        """from_azure handles missing optional fields."""
        op = OperationData.from_azure({"name": "Microsoft.Test/action"})
        assert op.name == "Microsoft.Test/action"
        assert op.display_name is None
        assert op.origin is None

    def test_very_long_operation_name(self):
        """OperationData handles very long operation names."""
        long_name = "Microsoft." + "A" * 500 + "/read"
        op = OperationData.model_validate({"name": long_name})
        assert op.name == long_name


# =============================================================================
# Azure HTTP and Auth Tests
# =============================================================================


class TestManagementUrl:
    """Tests for management_url helper."""

    def test_prepends_base_url(self):
        from rbaccatalog.azure.http import management_url

        result = management_url("/providers/Microsoft.Authorization")
        assert result == "https://management.azure.com/providers/Microsoft.Authorization"

    def test_handles_path_without_leading_slash(self):
        from rbaccatalog.azure.http import management_url

        result = management_url("providers/Microsoft.Authorization")
        assert result == "https://management.azure.com/providers/Microsoft.Authorization"


class TestAzureAuthContext:
    """Tests for Azure authentication context managers."""

    @pytest.mark.asyncio
    async def test_default_azure_credential_context_manager(self):
        """default_azure_credential yields and closes credential."""
        from unittest.mock import AsyncMock, patch

        mock_credential = AsyncMock()
        mock_credential.close = AsyncMock()

        with patch(
            "rbaccatalog.azure.auth.DefaultAzureCredential",
            return_value=mock_credential,
        ):
            from rbaccatalog.azure.auth import default_azure_credential

            async with default_azure_credential() as cred:
                assert cred is mock_credential

            mock_credential.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_azure_credential_pins_user_assigned_identity(self):
        """With managed identity enabled, msi_client_id pins the UAMI."""
        from unittest.mock import AsyncMock, patch

        from rbaccatalog.settings import Settings

        mock_credential = AsyncMock()
        settings = Settings.get()

        with (
            patch.object(settings, "use_managed_identity", True),
            patch.object(settings, "msi_client_id", "uami-client-id"),
            patch(
                "rbaccatalog.azure.auth.ManagedIdentityCredential",
                return_value=mock_credential,
            ) as mock_cls,
        ):
            from rbaccatalog.azure.auth import default_azure_credential

            async with default_azure_credential():
                pass

        mock_cls.assert_called_once_with(client_id="uami-client-id")

    @pytest.mark.asyncio
    async def test_default_azure_credential_disabled_uses_default_chain(self):
        """With managed identity disabled, the default credential chain is used."""
        from unittest.mock import AsyncMock, patch

        from rbaccatalog.settings import Settings

        mock_credential = AsyncMock()

        with (
            patch.object(Settings.get(), "use_managed_identity", False),
            patch(
                "rbaccatalog.azure.auth.DefaultAzureCredential",
                return_value=mock_credential,
            ) as mock_cls,
        ):
            from rbaccatalog.azure.auth import default_azure_credential

            async with default_azure_credential():
                pass

        mock_cls.assert_called_once_with()


# =============================================================================
# Azure Fetch Functions (Mocked)
# =============================================================================


def _make_mock_async_client(response_or_side_effect, *, method: str = "post"):
    """Create a mock async client with the given response or side effect."""
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    mock_method = getattr(mock_client, method)

    if isinstance(response_or_side_effect, (Exception, list)):
        mock_method.side_effect = response_or_side_effect
    else:
        mock_method.return_value = response_or_side_effect

    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    return mock_client


def _make_mock_response(json_data):
    """Create a mock response with json data."""
    from unittest.mock import MagicMock

    mock_response = MagicMock()
    mock_response.json.return_value = json_data
    mock_response.raise_for_status = MagicMock()
    return mock_response


class TestFetchBuiltinRoles:
    """Tests for fetch_builtin_roles with mocked Azure API."""

    @pytest.fixture
    def mock_role_response(self):
        """Standard role response for RBAC API format."""
        return {
            "id": "/providers/Microsoft.Authorization/roleDefinitions/abc-123",
            "name": "abc-123",
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {
                "roleName": "Reader",
                "type": "BuiltInRole",
                "description": "Can view resources",
                "permissions": [{"actions": ["*/read"], "notActions": []}],
            },
        }

    @pytest.mark.asyncio
    async def test_returns_role_definitions_on_success(self, mock_role_response):
        from unittest.mock import patch

        from rbaccatalog.azure.models import RoleDefinition

        response = _make_mock_response({"value": [mock_role_response]})
        mock_client = _make_mock_async_client(response, method="get")

        with patch(
            "rbaccatalog.azure.roles.authenticated_management_async_client",
            return_value=mock_client,
        ):
            from rbaccatalog.azure.roles import fetch_builtin_roles

            roles = await fetch_builtin_roles()

        assert len(roles) == 1
        assert isinstance(roles[0], RoleDefinition)
        assert roles[0].role_name == "Reader"
        assert roles[0].role_id == "abc-123"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "page_count",
        [2, 3, 5],
        ids=["2_pages", "3_pages", "5_pages"],
    )
    async def test_handles_pagination_with_next_link(self, page_count):
        from unittest.mock import patch

        import httpx

        responses: list[httpx.Response] = []
        for i in range(page_count):
            data: dict[str, object] = {
                "value": [
                    {
                        "id": f"/providers/Microsoft.Authorization/roleDefinitions/role-{i}",
                        "name": f"role-{i}",
                        "type": "Microsoft.Authorization/roleDefinitions",
                        "properties": {
                            "roleName": f"Role{i}",
                            "type": "BuiltInRole",
                            "permissions": [],
                        },
                    }
                ],
            }
            if i < page_count - 1:
                data["nextLink"] = f"https://management.azure.com/next?page={i + 2}"
            responses.append(_make_mock_response(data))

        mock_client = _make_mock_async_client(responses, method="get")

        with patch(
            "rbaccatalog.azure.roles.authenticated_management_async_client",
            return_value=mock_client,
        ):
            from rbaccatalog.azure.roles import fetch_builtin_roles

            roles = await fetch_builtin_roles()

        assert len(roles) == page_count
        assert mock_client.get.call_count == page_count

    @pytest.mark.asyncio
    async def test_uses_next_link_url_directly(self):
        """Verify that nextLink URL is used directly without modification."""
        from unittest.mock import patch

        next_link_url = "https://management.azure.com/providers/Microsoft.Authorization/roleDefinitions?$skiptoken=abc123"

        first_response = _make_mock_response(
            {
                "value": [
                    {
                        "id": "/test/1",
                        "name": "1",
                        "properties": {"roleName": "R1", "type": "BuiltInRole", "permissions": []},
                    }
                ],
                "nextLink": next_link_url,
            }
        )
        second_response = _make_mock_response({"value": []})

        mock_client = _make_mock_async_client([first_response, second_response], method="get")

        with patch(
            "rbaccatalog.azure.roles.authenticated_management_async_client",
            return_value=mock_client,
        ):
            from rbaccatalog.azure.roles import fetch_builtin_roles

            await fetch_builtin_roles()

        # Second call should use the nextLink URL directly
        assert mock_client.get.call_count == 2
        second_call_args = mock_client.get.call_args_list[1]
        assert second_call_args[0][0] == next_link_url

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error_type",
        [
            pytest.param("http_status", id="http_status_error"),
            pytest.param("connection", id="connection_error"),
        ],
    )
    async def test_raises_on_error(self, error_type):
        from unittest.mock import MagicMock, patch

        import httpx

        if error_type == "http_status":
            error = httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock())
        else:
            error = httpx.ConnectError("Connection failed")

        mock_client = _make_mock_async_client(error, method="get")

        with (
            patch(
                "rbaccatalog.azure.roles.authenticated_management_async_client",
                return_value=mock_client,
            ),
            pytest.raises((httpx.HTTPStatusError, httpx.ConnectError)),
        ):
            from rbaccatalog.azure.roles import fetch_builtin_roles

            await fetch_builtin_roles()


class TestFetchProviderOperations:
    """Tests for fetch_provider_operations with mocked Azure API."""

    @pytest.mark.asyncio
    async def test_returns_operations_on_success(self):
        from unittest.mock import patch

        response = _make_mock_response(
            {
                "value": [
                    {
                        "displayName": "Microsoft Compute",
                        "operations": [
                            {
                                "name": "Microsoft.Compute/register/action",
                                "displayName": "Register",
                                "isDataAction": False,
                            }
                        ],
                        "resourceTypes": [],
                    }
                ]
            }
        )
        mock_client = _make_mock_async_client(response, method="get")

        with patch(
            "rbaccatalog.azure.operations.authenticated_management_async_client",
            return_value=mock_client,
        ):
            from rbaccatalog.azure.operations import fetch_provider_operations

            operations = await fetch_provider_operations()

        assert len(operations) == 1
        assert operations[0].name == "Microsoft.Compute/register/action"


class TestAzureFetchErrorHandling:
    """Consolidated error handling tests for Azure fetch functions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "module_path,function_name,http_method,patch_target",
        [
            (
                "rbaccatalog.azure.roles",
                "fetch_builtin_roles",
                "get",
                "rbaccatalog.azure.roles.authenticated_management_async_client",
            ),
            (
                "rbaccatalog.azure.operations",
                "fetch_provider_operations",
                "get",
                "rbaccatalog.azure.operations.authenticated_management_async_client",
            ),
        ],
    )
    async def test_raises_on_api_error(self, module_path, function_name, http_method, patch_target):
        from importlib import import_module
        from unittest.mock import MagicMock, patch

        import httpx

        error = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_client = _make_mock_async_client(error, method=http_method)

        with patch(patch_target, return_value=mock_client):
            module = import_module(module_path)
            func = getattr(module, function_name)

            with pytest.raises(httpx.HTTPStatusError):
                await func()


class TestIsRetryableAzureError:
    """Tests for is_retryable_azure_error helper function."""

    def test_transport_error_is_retryable(self):
        """Test that TransportError is retryable."""
        import httpx

        from rbaccatalog.azure.http import is_retryable_azure_error

        exc = httpx.ConnectError("Connection failed")
        assert is_retryable_azure_error(exc) is True

    def test_timeout_error_is_retryable(self):
        """Test that TimeoutError is retryable (subclass of TransportError)."""
        import httpx

        from rbaccatalog.azure.http import is_retryable_azure_error

        exc = httpx.TimeoutException("Request timed out")
        assert is_retryable_azure_error(exc) is True

    @pytest.mark.parametrize(
        "status_code",
        [408, 429, 500, 502, 503, 504],
        ids=[
            "408_request_timeout",
            "429_ratelimit",
            "500_server_error",
            "502_bad_gateway",
            "503_unavailable",
            "504_timeout",
        ],
    )
    def test_retryable_http_status_codes(self, status_code):
        """Test that retryable HTTP status codes are recognized."""
        from unittest.mock import MagicMock

        import httpx

        from rbaccatalog.azure.http import is_retryable_azure_error

        mock_response = MagicMock()
        mock_response.status_code = status_code
        exc = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)

        assert is_retryable_azure_error(exc) is True

    @pytest.mark.parametrize(
        "status_code",
        [400, 401, 403, 404, 409, 422],
        ids=[
            "400_bad_request",
            "401_unauthorized",
            "403_forbidden",
            "404_not_found",
            "409_conflict",
            "422_unprocessable",
        ],
    )
    def test_non_retryable_http_status_codes(self, status_code):
        """Test that non-retryable HTTP status codes are not retried."""
        from unittest.mock import MagicMock

        import httpx

        from rbaccatalog.azure.http import is_retryable_azure_error

        mock_response = MagicMock()
        mock_response.status_code = status_code
        exc = httpx.HTTPStatusError("Error", request=MagicMock(), response=mock_response)

        assert is_retryable_azure_error(exc) is False

    def test_other_exceptions_not_retryable(self):
        """Test that non-httpx exceptions are not retryable."""
        from rbaccatalog.azure.http import is_retryable_azure_error

        exc = ValueError("Some other error")
        assert is_retryable_azure_error(exc) is False


class TestAzureFetchRetryLogic:
    """Tests for retry behavior on Azure fetch functions."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "module_path,function_name",
        [
            pytest.param(
                "rbaccatalog.azure.roles",
                "fetch_builtin_roles",
                id="fetch_builtin_roles",
            ),
            pytest.param(
                "rbaccatalog.azure.operations",
                "fetch_provider_operations",
                id="fetch_provider_operations",
            ),
        ],
    )
    async def test_fetch_functions_retry_on_transport_error(self, module_path, function_name):
        """Test that Azure fetch functions retry on transport errors."""
        from importlib import import_module
        from unittest.mock import patch

        import httpx

        success_response = _make_mock_response({"value": []})

        with (
            patch(
                f"{module_path}.authenticated_management_async_client",
                side_effect=[
                    httpx.ConnectError("Connection failed"),
                    _make_mock_async_client(success_response, method="get"),
                ],
            ),
            patch("tenacity.nap.time.sleep"),
        ):
            module = import_module(module_path)
            func = getattr(module, function_name)
            result = await func()
            assert len(result) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status_code,should_retry",
        [
            pytest.param(408, True, id="408_request_timeout_retries"),
            pytest.param(429, True, id="429_rate_limit_retries"),
            pytest.param(500, True, id="500_server_error_retries"),
            pytest.param(502, True, id="502_bad_gateway_retries"),
            pytest.param(503, True, id="503_unavailable_retries"),
            pytest.param(504, True, id="504_timeout_retries"),
            pytest.param(401, False, id="401_auth_fails_immediately"),
            pytest.param(403, False, id="403_forbidden_fails_immediately"),
            pytest.param(404, False, id="404_not_found_fails_immediately"),
        ],
    )
    async def test_fetch_builtin_roles_retry_by_status_code(self, status_code, should_retry):
        """Test that fetch_builtin_roles retries appropriately based on status code."""
        from unittest.mock import MagicMock, patch

        import httpx

        http_response = MagicMock()
        http_response.status_code = status_code
        error = httpx.HTTPStatusError("Error", request=MagicMock(), response=http_response)

        if should_retry:
            success_response = _make_mock_response({"value": []})
            clients = [
                _make_mock_async_client(error, method="get"),
                _make_mock_async_client(success_response, method="get"),
            ]
            client_iter = iter(clients)
        else:
            clients = [_make_mock_async_client(error, method="get")]
            client_iter = iter(clients)

        with (
            patch(
                "rbaccatalog.azure.roles.authenticated_management_async_client",
                side_effect=lambda **kwargs: next(client_iter),
            ),
            patch("tenacity.nap.time.sleep"),
        ):
            if not should_retry:
                with pytest.raises(httpx.HTTPStatusError):
                    from rbaccatalog.azure.roles import fetch_builtin_roles

                    await fetch_builtin_roles()
            else:
                from rbaccatalog.azure.roles import fetch_builtin_roles

                result = await fetch_builtin_roles()
                assert len(result) == 0
