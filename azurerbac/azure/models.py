"""Pydantic models for Azure RBAC role definitions and operations."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Mapping of lowercase field names to canonical Python attribute names
_PERMISSION_FIELD_MAP = {
    "actions": "actions",
    "dataactions": "data_actions",
    "notactions": "not_actions",
    "notdataactions": "not_data_actions",
    "condition": "condition",
    "conditionversion": "condition_version",
}

_ROLE_PROPERTIES_FIELD_MAP = {
    "rolename": "role_name",
    "type": "type",
    "description": "description",
    "assignablescopes": "assignable_scopes",
    "permissions": "permissions",
    "createdon": "created_on",
    "updatedon": "updated_on",
    "createdby": "created_by",
    "updatedby": "updated_by",
}

_ROLE_DEFINITION_FIELD_MAP = {
    "id": "id",
    "name": "name",
    "type": "type",
    "properties": "properties",
}


class Permission(BaseModel):
    """Azure RBAC permission block with case-insensitive field parsing.

    Supports any case variation of field names:
    - PascalCase (Azure Resource Graph): DataActions, NotActions
    - camelCase (Azure REST API, serialization): dataActions, notActions
    - lowercase: dataactions, notactions
    - snake_case (Python): data_actions, not_actions
    """

    model_config = ConfigDict(populate_by_name=True)

    actions: list[str] = Field(default_factory=list)
    data_actions: list[str] = Field(default_factory=list, serialization_alias="dataActions")
    not_actions: list[str] = Field(default_factory=list, serialization_alias="notActions")
    not_data_actions: list[str] = Field(default_factory=list, serialization_alias="notDataActions")
    condition: str | None = Field(default=None)
    condition_version: str | None = Field(default=None, serialization_alias="conditionVersion")

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        """Normalize field names to lowercase for case-insensitive matching.

        Also converts None to empty list for list fields (Azure API sometimes returns null).
        """
        if not isinstance(data, dict):
            return data
        # Fields that should be lists (None -> [])
        list_fields = {"actions", "data_actions", "not_actions", "not_data_actions"}
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            folded_key = key.casefold()
            if folded_key in _PERMISSION_FIELD_MAP:
                target_key = _PERMISSION_FIELD_MAP[folded_key]
                # Convert None to [] for list fields
                if target_key in list_fields and value is None:
                    normalized[target_key] = []
                else:
                    normalized[target_key] = value
            else:
                # Keep unknown keys as-is (Pydantic will ignore them with default config)
                normalized[key] = value
        return normalized

    def to_dict(self) -> dict[str, Any]:
        """Export to dict with canonical field names and order matching Azure API."""
        result: dict[str, Any] = {
            "actions": self.actions,
            "notActions": self.not_actions,
            "dataActions": self.data_actions,
            "notDataActions": self.not_data_actions,
        }
        if self.condition:
            result["condition"] = self.condition
        if self.condition_version:
            result["conditionVersion"] = self.condition_version
        return result

    def to_comparable_dict(self) -> dict[str, Any]:
        """Export to dict with sorted lists for comparison/diffing."""
        result: dict[str, Any] = {
            "actions": sorted(self.actions),
            "notActions": sorted(self.not_actions),
            "dataActions": sorted(self.data_actions),
            "notDataActions": sorted(self.not_data_actions),
        }
        if self.condition:
            result["condition"] = self.condition
        if self.condition_version:
            result["conditionVersion"] = self.condition_version
        return result

    @property
    def has_condition(self) -> bool:
        """Check if this permission has an ABAC condition."""
        return bool(self.condition)


class RoleProperties(BaseModel):
    """Properties of an Azure role definition with case-insensitive field parsing.

    Supports any case variation of field names:
    - PascalCase (Azure Resource Graph): RoleName, AssignableScopes
    - camelCase (Azure REST API, serialization): roleName, assignableScopes
    - lowercase: rolename, assignablescopes
    - snake_case (Python): role_name, assignable_scopes
    """

    model_config = ConfigDict(populate_by_name=True)

    role_name: str = Field(default="", alias="roleName")
    type: str = Field(default="")
    description: str = Field(default="")
    assignable_scopes: list[str] = Field(default_factory=list, alias="assignableScopes")
    permissions: list[Permission] = Field(default_factory=list)
    created_on: dt.datetime | None = Field(default=None, alias="createdOn")
    updated_on: dt.datetime | None = Field(default=None, alias="updatedOn")
    created_by: str | None = Field(default=None, alias="createdBy")
    updated_by: str | None = Field(default=None, alias="updatedBy")

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        """Normalize field names to lowercase for case-insensitive matching."""
        if not isinstance(data, dict):
            return data
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            folded_key = key.casefold()
            if folded_key in _ROLE_PROPERTIES_FIELD_MAP:
                normalized[_ROLE_PROPERTIES_FIELD_MAP[folded_key]] = value
            else:
                normalized[key] = value
        return normalized

    @field_validator("created_on", "updated_on", mode="before")
    @classmethod
    def _parse_datetime(cls, v: str | dt.datetime | None) -> dt.datetime | None:
        """Parse datetime, treating empty/invalid strings as None."""
        if v is None or v == "":
            return None
        if isinstance(v, dt.datetime):
            return v
        # Try to parse string
        try:
            return dt.datetime.fromisoformat(v)
        except (ValueError, TypeError):
            return None


class RoleDefinition(BaseModel):
    """Azure RBAC role definition with case-insensitive field parsing.

    Supports any case variation of field names:
    - PascalCase: Id, Name, Type, Properties
    - camelCase: id, name, type, properties
    - lowercase: id, name, type, properties
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="")
    name: str = Field(default="")
    type: str = Field(default="Microsoft.Authorization/roleDefinitions")
    properties: RoleProperties = Field(default_factory=RoleProperties)

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        """Normalize field names to lowercase for case-insensitive matching."""
        if not isinstance(data, dict):
            return data
        normalized: dict[str, Any] = {}
        for key, value in data.items():
            folded_key = key.casefold()
            if folded_key in _ROLE_DEFINITION_FIELD_MAP:
                normalized[_ROLE_DEFINITION_FIELD_MAP[folded_key]] = value
            else:
                normalized[key] = value
        return normalized

    @classmethod
    def from_resource_graph(cls, item: dict[str, Any]) -> RoleDefinition:
        """Transform a Resource Graph role definition to normalized format.

        Resource Graph returns:
            {
                "id": "/providers/Microsoft.Authorization/RoleDefinitions/...",
                "properties": { ... }
            }

        Normalizes:
            - id casing (RoleDefinitions -> roleDefinitions)
            - Extracts GUID as name
            - Parses permissions with case-insensitive handling
            - Excludes isServiceRole
        """
        role_id = item.get("id", "")
        props = item.get("properties", {})

        # Extract the GUID from the id (last segment)
        name = role_id.rsplit("/", 1)[-1] if "/" in role_id else role_id

        # Normalize the id path casing (RoleDefinitions -> roleDefinitions)
        normalized_id = role_id.replace(
            "/Microsoft.Authorization/RoleDefinitions/",
            "/Microsoft.Authorization/roleDefinitions/",
        )

        return cls(
            id=normalized_id,
            name=name,
            type="Microsoft.Authorization/roleDefinitions",
            properties=RoleProperties.model_validate(
                {
                    "roleName": props.get("roleName", ""),
                    "type": props.get("type", ""),
                    "description": props.get("description", ""),
                    "assignableScopes": props.get("assignableScopes", []),
                    "permissions": [
                        Permission.model_validate(p) for p in props.get("permissions", [])
                    ],
                    "createdOn": props.get("createdOn", ""),
                    "updatedOn": props.get("updatedOn", ""),
                    "createdBy": props.get("createdBy"),
                    "updatedBy": props.get("updatedBy"),
                    # isServiceRole intentionally excluded
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Export to dict with canonical field names and order matching Azure API."""
        props = self.properties
        return {
            "id": self.id,
            "name": self.name,
            "properties": {
                "assignableScopes": props.assignable_scopes,
                "createdBy": props.created_by,
                "createdOn": props.created_on.isoformat() if props.created_on else None,
                "description": props.description,
                "permissions": [p.to_dict() for p in props.permissions],
                "roleName": props.role_name,
                "type": props.type,
                "updatedBy": props.updated_by,
                "updatedOn": props.updated_on.isoformat() if props.updated_on else None,
            },
            "type": self.type,
        }

    @property
    def role_id(self) -> str:
        """Get the role ID (GUID). Alias for 'name' field."""
        return self.name

    @property
    def role_name(self) -> str:
        """Get the role display name."""
        return self.properties.role_name

    @property
    def description(self) -> str:
        """Get the role description."""
        return self.properties.description

    @property
    def role_type(self) -> str:
        """Get the role type (BuiltInRole, CustomRole, etc.)."""
        return self.properties.type

    @property
    def is_builtin(self) -> bool:
        """Check if this is a built-in role."""
        return self.properties.type == "BuiltInRole"


class OperationData(BaseModel):
    """Azure provider operation (permission) - parsed from Azure API.

    All operation fields are captured directly from the Azure API.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(default="")
    display_name: str | None = Field(default=None, alias="displayName")
    description: str | None = Field(default=None)
    origin: str | None = Field(default=None)
    is_data_action: bool = Field(default=False, alias="isDataAction")

    # Context from parent provider/resource type
    provider_display_name: str = Field(default="")
    resource_type: str | None = Field(default=None)
    resource_type_display_name: str | None = Field(default=None)

    @classmethod
    def from_azure(
        cls,
        op: dict[str, Any],
        *,
        provider_display_name: str = "",
        resource_type: str | None = None,
        resource_type_display_name: str | None = None,
    ) -> OperationData:
        """Parse operation from Azure API response."""
        return cls.model_validate(
            {
                "name": op.get("name", ""),
                "displayName": op.get("displayName"),
                "description": op.get("description"),
                "origin": op.get("origin"),
                "isDataAction": op.get("isDataAction", False),
                "provider_display_name": provider_display_name,
                "resource_type": resource_type,
                "resource_type_display_name": resource_type_display_name,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Export to dict for database storage."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "origin": self.origin,
            "provider_display_name": self.provider_display_name,
            "resource_type": self.resource_type,
            "resource_type_display_name": self.resource_type_display_name,
            "is_data_action": self.is_data_action,
        }
