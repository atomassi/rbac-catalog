"""Pydantic models for Azure RBAC role definitions and operations."""

import datetime as dt
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from azurerbac.core.constants import DEFAULT_ROLE_TYPE, ROLE_DEFINITION_TYPE
from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_iso_z

# Mapping of lowercase field names to canonical Python attribute names
_PERMISSION_FIELD_MAP = {
    "actions": "actions",
    "dataactions": "data_actions",
    "notactions": "not_actions",
    "notdataactions": "not_data_actions",
    "condition": "condition",
    "conditionversion": "condition_version",
}

# Fields that should be lists (None -> [] for Azure API null handling)
_PERMISSION_LIST_FIELDS = frozenset({"actions", "data_actions", "not_actions", "not_data_actions"})

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


def _normalize_dict_keys(data: Any, field_map: dict[str, str]) -> dict[str, Any]:
    """Normalize dict keys using case-insensitive mapping."""
    if not isinstance(data, dict):
        return data
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        folded_key = key.casefold()
        if folded_key in field_map:
            normalized[field_map[folded_key]] = value
        else:
            normalized[key] = value
    return normalized


class Permission(BaseModel):
    """Azure RBAC permission block with case-insensitive field parsing."""

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
        """Normalize field names and convert None to [] for list fields.

        Azure API sometimes returns null for empty permission arrays.
        """
        normalized = _normalize_dict_keys(data, _PERMISSION_FIELD_MAP)
        if isinstance(normalized, dict):
            for field in _PERMISSION_LIST_FIELDS:
                if normalized.get(field) is None:
                    normalized[field] = []
        return normalized

    def to_dict(self) -> JsonDict:
        """Export to dict with canonical field names and order matching Azure API."""
        result: JsonDict = {
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

    def to_comparable_dict(self) -> JsonDict:
        """Export to dict with sorted lists for comparison/diffing."""
        result = self.to_dict()
        for key in ("actions", "notActions", "dataActions", "notDataActions"):
            result[key] = sorted(result[key])
        return result

    @property
    def has_condition(self) -> bool:
        """Check if this permission has an ABAC condition."""
        return bool(self.condition)


class RoleProperties(BaseModel):
    """Properties of an Azure role definition with case-insensitive field parsing."""

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
        return _normalize_dict_keys(data, _ROLE_PROPERTIES_FIELD_MAP)

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
    """Azure RBAC role definition with case-insensitive field parsing."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="")
    name: str = Field(default="")
    type: str = Field(default=ROLE_DEFINITION_TYPE)
    properties: RoleProperties = Field(default_factory=RoleProperties)

    @model_validator(mode="before")
    @classmethod
    def _normalize_keys(cls, data: Any) -> Any:
        """Normalize field names to lowercase for case-insensitive matching."""
        return _normalize_dict_keys(data, _ROLE_DEFINITION_FIELD_MAP)

    @classmethod
    def from_rbac_api(cls, item: dict[str, Any]) -> Self:
        """Transform an RBAC API role definition response to normalized format."""
        return cls(
            id=item.get("id", ""),
            name=item.get("name", ""),
            type=item.get("type", ROLE_DEFINITION_TYPE),
            properties=RoleProperties.model_validate(item.get("properties", {})),
        )

    def to_dict(self) -> JsonDict:
        """Export to dict with canonical field names and order matching Azure API."""
        props = self.properties
        return {
            "id": self.id,
            "name": self.name,
            "properties": {
                "assignableScopes": props.assignable_scopes,
                "createdBy": props.created_by,
                "createdOn": format_iso_z(props.created_on),
                "description": props.description,
                "permissions": [p.to_dict() for p in props.permissions],
                "roleName": props.role_name,
                "type": props.type,
                "updatedBy": props.updated_by,
                "updatedOn": format_iso_z(props.updated_on),
            },
            "type": self.type,
        }

    @property
    def role_id(self) -> str:
        """Role ID (GUID) - alias for 'name' field."""
        return self.name

    @property
    def role_name(self) -> str:
        return self.properties.role_name

    @property
    def description(self) -> str:
        return self.properties.description

    @property
    def role_type(self) -> str:
        return self.properties.type

    @property
    def is_builtin(self) -> bool:
        return self.properties.type == DEFAULT_ROLE_TYPE


class OperationData(BaseModel):
    """Azure provider operation (permission) - parsed from Azure API.

    All operation fields are captured directly from the Azure API.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(default="")
    display_name: str | None = Field(
        default=None, validation_alias="displayName", serialization_alias="displayName"
    )
    description: str | None = Field(default=None)
    origin: str | None = Field(default=None)
    is_data_action: bool = Field(
        default=False, validation_alias="isDataAction", serialization_alias="isDataAction"
    )

    # Context from parent provider/resource type
    provider_display_name: str = Field(default="")
    resource_type: str | None = Field(default=None)
    resource_type_display_name: str | None = Field(default=None)

    # Pre-computed lowercase search text for fast matching
    _search_text: str = PrivateAttr(default="")
    _name_lower: str = PrivateAttr(default="")

    @model_validator(mode="after")
    def _compute_search_text(self) -> Self:
        """Pre-compute lowercased search text for fast matching."""
        self._name_lower = self.name.lower()
        parts = [
            self.name,
            self.display_name or "",
            self.description or "",
            self.provider_display_name,
            self.resource_type_display_name or "",
        ]
        self._search_text = " ".join(parts).lower()
        return self

    @classmethod
    def from_azure(
        cls,
        op: dict[str, Any],
        *,
        provider_display_name: str = "",
        resource_type: str | None = None,
        resource_type_display_name: str | None = None,
    ) -> Self:
        """Parse operation from Azure API response."""
        return cls.model_validate(
            {
                "name": op.get("name", ""),
                "display_name": op.get("displayName"),
                "description": op.get("description"),
                "origin": op.get("origin"),
                "is_data_action": op.get("isDataAction", False),
                "provider_display_name": provider_display_name,
                "resource_type": resource_type,
                "resource_type_display_name": resource_type_display_name,
            }
        )

    def to_dict(self) -> JsonDict:
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

    @property
    def name_lower(self) -> str:
        """Pre-computed lowercase name for fast wildcard matching."""
        return self._name_lower

    def matches_search(self, query_lower: str) -> bool:
        """Check if operation matches a text search query (case-insensitive)."""
        return query_lower in self._search_text
