"""Tests for the Azure API modules (operations and roles)."""

from __future__ import annotations

import pytest

from azurerbac.azure.roles import _transform_resource_graph_role


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
        assert provider_op["name"] == "Microsoft.Compute/register/action"
        assert provider_op["provider_display_name"] == "Microsoft Compute"
        assert provider_op["resource_type"] is None
        assert provider_op["resource_type_display_name"] is None
        assert provider_op["is_data_action"] is False

        rt_ops = operations[1:]
        assert all(op["resource_type"] == "virtualMachines" for op in rt_ops)
        assert all(op["resource_type_display_name"] == "Virtual Machines" for op in rt_ops)

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
        """Test that Condition and ConditionVersion are preserved."""
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
        assert perms["Condition"] == "@Resource[Microsoft.Storage/storageAccounts:name] == 'test'"
        assert perms["ConditionVersion"] == "2.0"


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
        assert props["createdOn"] == "2020-01-01T00:00:00Z"
        assert props["updatedOn"] == "2021-01-01T00:00:00Z"
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
