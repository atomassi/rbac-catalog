from __future__ import annotations

import logging

from azurerbac.azure.http import authenticated_management_async_client, management_url
from azurerbac.azure.models import RoleDefinition
from azurerbac.settings import get_settings

logger = logging.getLogger(__name__)

_RESOURCE_GRAPH_QUERY = """
authorizationresources
| where type == "microsoft.authorization/roledefinitions"
| where properties.type == "BuiltInRole"
| where properties.isServiceRole == false
| project id, properties
"""

# RBAC API constants
_RBAC_API_VERSION = "2022-05-01-preview"
_RBAC_FILTER = "type eq 'BuiltInRole'"


async def fetch_builtin_roles() -> list[RoleDefinition]:
    """Fetch all built-in role definitions using configured method.

    Uses RBAC API if USE_RBAC_API=true, otherwise uses Resource Graph.
    """
    if get_settings().use_rbac_api:
        return await fetch_builtin_roles_rbac_api()
    return await fetch_builtin_roles_resource_graph()


async def fetch_builtin_roles_resource_graph() -> list[RoleDefinition]:
    """Fetch all built-in role definitions via Azure Resource Graph."""
    roles: list[RoleDefinition] = []
    url = management_url("/providers/Microsoft.ResourceGraph/resources")
    params = {"api-version": "2022-10-01"}

    logger.info("Fetching roles via Azure Resource Graph")

    try:
        async with authenticated_management_async_client(timeout=120.0) as client:
            skip_token: str | None = None
            while True:
                body: dict = {
                    "query": _RESOURCE_GRAPH_QUERY,
                    "options": {"resultFormat": "objectArray", "$top": 1000},
                }
                if skip_token:
                    body["options"]["$skipToken"] = skip_token

                response = await client.post(url, params=params, json=body)
                response.raise_for_status()
                data = response.json()

                for item in data.get("data", []):
                    roles.append(RoleDefinition.from_resource_graph(item))

                skip_token = data.get("$skipToken")
                if not skip_token:
                    break
    except Exception:
        logger.exception("Failed to fetch built-in roles via Resource Graph")
        raise

    logger.info("Total built-in roles fetched via Resource Graph: %d", len(roles))
    return roles


async def fetch_builtin_roles_rbac_api() -> list[RoleDefinition]:
    """Fetch all built-in role definitions via RBAC API.

    Uses the direct RBAC API endpoint:
    GET /providers/Microsoft.Authorization/roleDefinitions?$filter=type eq 'BuiltInRole'

    This method is an alternative to Resource Graph that doesn't require
    Reader access to any subscription.
    """
    roles: list[RoleDefinition] = []
    url = management_url("/providers/Microsoft.Authorization/roleDefinitions")
    params = {
        "api-version": _RBAC_API_VERSION,
        "$filter": _RBAC_FILTER,
    }

    logger.info("Fetching roles via RBAC API")

    try:
        async with authenticated_management_async_client(timeout=120.0) as client:
            next_link: str | None = None
            while True:
                if next_link:
                    # nextLink is a full URL, use it directly
                    response = await client.get(next_link)
                else:
                    response = await client.get(url, params=params)

                response.raise_for_status()
                data = response.json()

                for item in data.get("value", []):
                    roles.append(RoleDefinition.from_rbac_api(item))

                next_link = data.get("nextLink")
                if not next_link:
                    break
    except Exception:
        logger.exception("Failed to fetch built-in roles via RBAC API")
        raise

    logger.info("Total built-in roles fetched via RBAC API: %d", len(roles))
    return roles
