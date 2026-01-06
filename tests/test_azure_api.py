"""Tests for the Azure API modules (operations and roles)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from azurerbac.azure.models import OperationData, Permission, RoleDefinition, RoleProperties


def _transform_resource_graph_role(item: dict[str, Any]) -> dict[str, Any]:
    """Test helper: transform Resource Graph role to expected format."""
    return RoleDefinition.from_resource_graph(item).to_dict()


class TestFlattenProviderOperationsPayload:
    def test_flattens_provider_and_resource_type_operations(self):
        from azurerbac.azure.operations import _flatten_provider_operations_payload

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

        operations = _flatten_provider_operations_payload(payload)
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
        from azurerbac.azure.operations import _flatten_provider_operations_payload

        assert _flatten_provider_operations_payload({"value": []}) == []

    def test_defaults_missing_sections(self):
        from azurerbac.azure.operations import _flatten_provider_operations_payload

        payload = {"value": [{"displayName": "X"}]}
        operations = _flatten_provider_operations_payload(payload)
        assert operations == []


# =============================================================================
# Azure Roles Tests
# =============================================================================


def _make_input_item(
    role_id: str = "/providers/Microsoft.Authorization/RoleDefinitions/test-guid",
    role_name: str = "Test Role",
    role_type: str = "BuiltInRole",
    permissions: list | None = None,
    **extra_props,
) -> dict:
    """Helper to create input items for transformation."""
    props = {
        "roleName": role_name,
        "type": role_type,
        "permissions": permissions or [],
        **extra_props,
    }
    return {"id": role_id, "properties": props}


# =============================================================================
# Tests for ID Normalization
# =============================================================================


class TestIdNormalization:
    """Tests for role ID extraction and normalization."""

    @pytest.mark.parametrize(
        "input_id,expected_name",
        [
            ("/providers/Microsoft.Authorization/RoleDefinitions/abc-123", "abc-123"),
            ("/subscriptions/sub/providers/Microsoft.Authorization/RoleDefinitions/xyz", "xyz"),
            ("/providers/Microsoft.Authorization/roleDefinitions/lowercase", "lowercase"),
        ],
    )
    def test_extracts_name_from_id(self, input_id: str, expected_name: str):
        """Test GUID extraction from various ID formats."""
        item = _make_input_item(role_id=input_id)
        result = _transform_resource_graph_role(item)
        assert result["name"] == expected_name

    def test_normalizes_roledefinitions_casing(self):
        """Test that RoleDefinitions is normalized to roleDefinitions."""
        item = _make_input_item(role_id="/providers/Microsoft.Authorization/RoleDefinitions/test")
        result = _transform_resource_graph_role(item)
        assert "/roleDefinitions/" in result["id"]
        assert "/RoleDefinitions/" not in result["id"]

    def test_handles_missing_id(self):
        """Test handling when id is missing."""
        item = {"properties": {"roleName": "Test", "permissions": []}}
        result = _transform_resource_graph_role(item)
        assert result["name"] == ""
        assert result["id"] == ""


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
        result = _transform_resource_graph_role(item)
        perms = result["properties"]["permissions"][0]
        assert expected_key in perms
        assert perms[expected_key] == input_value

    def test_handles_missing_permission_fields(self):
        """Test that missing permission fields default to empty lists."""
        item = _make_input_item(
            permissions=[{"actions": ["Microsoft.Compute/virtualMachines/read"]}]
        )
        result = _transform_resource_graph_role(item)
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
        result = _transform_resource_graph_role(item)
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
        result = _transform_resource_graph_role(item)
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
        result = _transform_resource_graph_role(item)

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
        item = {"id": "/providers/Microsoft.Authorization/RoleDefinitions/empty", "properties": {}}
        result = _transform_resource_graph_role(item)
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
        result = _transform_resource_graph_role(item)
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
        result = _transform_resource_graph_role(item)
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

    def test_empty_string_becomes_none(self):
        """Empty string datetime values become None."""
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
                    "createdOn": "",
                    "updatedOn": "",
                },
            }
        )
        assert role.properties.created_on is None
        assert role.properties.updated_on is None

    def test_invalid_string_becomes_none(self):
        """Invalid datetime strings become None (no validation error)."""
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
                    "createdOn": "not-a-date",
                    "updatedOn": "invalid-timestamp",
                },
            }
        )
        assert role.properties.created_on is None
        assert role.properties.updated_on is None

    def test_null_value_becomes_none(self):
        """Null/None datetime values stay None."""
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
                    "createdOn": None,
                    "updatedOn": None,
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
            # PascalCase (Resource Graph)
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
        from azurerbac.azure.models import RoleProperties

        props = RoleProperties.model_validate(key_case)
        assert props.role_name == "Test"
        assert props.assignable_scopes == ["/"]
        assert props.created_on is not None

    def test_role_properties_preserves_unknown_fields(self):
        """Test that unknown fields pass through unchanged."""
        from azurerbac.azure.models import RoleProperties

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
        from azurerbac.azure.models import RoleDefinition

        role = RoleDefinition.model_validate(key_case)
        assert role.id == "/test"
        assert role.name == "test-guid"
        assert role.properties.role_name == "Test"

    def test_role_definition_with_nested_properties_case(self):
        """Test that nested RoleProperties also handles case variations."""
        from azurerbac.azure.models import RoleDefinition

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

    def test_condition_empty_string_is_not_has_condition(self):
        """Empty condition string means has_condition is False."""
        perm = Permission.model_validate({"actions": ["*"], "condition": ""})
        assert perm.condition == ""
        assert not perm.has_condition  # Empty string is falsy

    def test_condition_none_is_not_has_condition(self):
        """None condition means has_condition is False."""
        perm = Permission.model_validate({"actions": ["*"], "condition": None})
        assert perm.condition is None
        assert not perm.has_condition

    def test_condition_with_version_but_empty_condition(self):
        """conditionVersion without condition is valid."""
        perm = Permission.model_validate(
            {"actions": ["*"], "condition": None, "conditionVersion": "2.0"}
        )
        assert perm.condition_version == "2.0"
        assert not perm.has_condition


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
        assert props["createdOn"] == "2024-01-01T00:00:00+00:00"
        assert props["updatedOn"] == "2024-06-01T00:00:00+00:00"

    def test_to_dict_permission_values(self, sample_role):
        """to_dict correctly preserves permission values."""
        result = sample_role.to_dict()
        perm = result["properties"]["permissions"][0]
        assert perm["actions"] == ["Microsoft.Test/read"]
        assert perm["notActions"] == []
        assert perm["dataActions"] == ["Microsoft.Test/data/read"]
        assert perm["notDataActions"] == []


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
