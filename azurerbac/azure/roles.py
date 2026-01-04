from __future__ import annotations

import logging
from typing import Any, Final

from azurerbac.azure.http import authenticated_management_async_client, management_url

# Permission field mappings: (output_key, lowercase_key, capitalized_key)
_PERMISSION_FIELDS: Final = (
    ("actions", "actions", "Actions"),
    ("notActions", "notActions", "NotActions"),
    ("dataActions", "dataActions", "DataActions"),
    ("notDataActions", "notDataActions", "NotDataActions"),
)


def get_role_id(role: dict[str, Any]) -> str:
    """Extract role ID (GUID) from a role dict."""
    return role.get("name", role.get("id", ""))


def get_role_properties(role: dict[str, Any]) -> dict[str, Any]:
    """Extract properties dict from a role dict."""
    return role.get("properties", {})


def get_role_name(role: dict[str, Any]) -> str:
    """Extract role display name from a role dict."""
    return get_role_properties(role).get("roleName", "")


def get_role_description(role: dict[str, Any]) -> str:
    """Extract role description from a role dict."""
    return get_role_properties(role).get("description", "")


def get_permission_condition(perm: dict[str, Any]) -> str | None:
    """Extract ABAC condition from a permission dict (case-insensitive)."""
    return perm.get("Condition") or perm.get("condition")


def get_permission_condition_version(perm: dict[str, Any]) -> str | None:
    """Extract ABAC condition version from a permission dict (case-insensitive)."""
    return perm.get("ConditionVersion") or perm.get("conditionVersion")


def has_any_condition(permissions: list[dict[str, Any]]) -> bool:
    """Check if any permission in the list has an ABAC condition."""
    return any(get_permission_condition(perm) for perm in permissions)


def get_permission_actions(perm: dict[str, Any]) -> tuple[list, list, list, list]:
    """Extract action lists from a single permission dict."""
    return (
        perm.get("actions", []) or [],
        perm.get("notActions", []) or [],
        perm.get("dataActions", []) or [],
        perm.get("notDataActions", []) or [],
    )


def extract_permission_lists(
    permissions: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Extract all permission lists from role permissions."""
    actions: list[str] = []
    not_actions: list[str] = []
    data_actions: list[str] = []
    not_data_actions: list[str] = []
    for perm in permissions:
        pa, pna, pda, pnda = get_permission_actions(perm)
        actions.extend(pa)
        not_actions.extend(pna)
        data_actions.extend(pda)
        not_data_actions.extend(pnda)
    return actions, not_actions, data_actions, not_data_actions


def _normalize_permission(perm: dict[str, Any]) -> dict[str, Any]:
    """Normalize a single permission block to consistent lowercase field names."""
    normalized = {
        key: perm.get(lower) or perm.get(cap) or [] for key, lower, cap in _PERMISSION_FIELDS
    }
    # Include ABAC condition fields if present
    condition = get_permission_condition(perm)
    condition_version = get_permission_condition_version(perm)
    if condition:
        normalized["Condition"] = condition
    if condition_version:
        normalized["ConditionVersion"] = condition_version
    return normalized


def _transform_resource_graph_role(item: dict[str, Any]) -> dict[str, Any]:
    """Transform a Resource Graph role definition to the expected format.

    Resource Graph returns:
        {
            "id": "/providers/Microsoft.Authorization/RoleDefinitions/...",
            "properties": { ... with lowercase permission fields ... }
        }

    Expected output format:
        {
            "id": "/providers/Microsoft.Authorization/roleDefinitions/...",
            "type": "Microsoft.Authorization/roleDefinitions",
            "name": "<guid>",
            "properties": { ... with lowercase permission fields ... }
        }
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

    # Normalize permission field names to lowercase
    normalized_permissions = [_normalize_permission(p) for p in props.get("permissions", [])]

    normalized_props = {
        "roleName": props.get("roleName", ""),
        "type": props.get("type", ""),
        "description": props.get("description", ""),
        "assignableScopes": props.get("assignableScopes", []),
        "permissions": normalized_permissions,
        "createdOn": props.get("createdOn", ""),
        "updatedOn": props.get("updatedOn", ""),
        "createdBy": props.get("createdBy"),
        "updatedBy": props.get("updatedBy"),
        "isServiceRole": props.get("isServiceRole", False),
    }

    return {
        "properties": normalized_props,
        "id": normalized_id,
        "type": "Microsoft.Authorization/roleDefinitions",
        "name": name,
    }


async def fetch_builtin_roles() -> list[dict[str, Any]]:
    """Fetch all built-in role definitions using Azure Resource Graph.

    Uses a Resource Graph query to fetch role definitions efficiently.
    """
    logger = logging.getLogger("azurerbac.azure_roles")

    roles: list[dict[str, Any]] = []

    # Resource Graph query for built-in roles (non-service roles only)
    query = """
authorizationresources
| where type == "microsoft.authorization/roledefinitions"
| where properties.type == "BuiltInRole"
| where properties.isServiceRole == false
| project id, properties
"""

    try:
        url = management_url("/providers/Microsoft.ResourceGraph/resources")
        params = {"api-version": "2022-10-01"}

        logger.info("Fetching roles via Azure Resource Graph")

        async with authenticated_management_async_client(timeout=120.0) as client:
            skip_token = None

            while True:
                body: dict[str, Any] = {
                    "query": query,
                    "options": {
                        "resultFormat": "objectArray",
                        "$top": 1000,
                    },
                }
                if skip_token:
                    body["options"]["$skipToken"] = skip_token

                logger.debug("POST %s body=%s", url, body)

                response = await client.post(
                    url,
                    params=params,
                    json=body,
                )
                response.raise_for_status()
                data = response.json()

                items = data.get("data", [])
                for item in items:
                    # Transform from Resource Graph format to expected format
                    transformed = _transform_resource_graph_role(item)
                    roles.append(transformed)

                # Check for pagination
                skip_token = data.get("$skipToken")
                if not skip_token:
                    break

    except Exception as e:
        logger.exception("Failed to fetch built-in roles via Resource Graph: %s", e)
        raise

    logger.info("Total built-in roles fetched: %d", len(roles))

    return roles
