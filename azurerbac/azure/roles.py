from __future__ import annotations

import logging

from azurerbac.azure.http import authenticated_management_async_client, management_url
from azurerbac.azure.models import RoleDefinition

logger = logging.getLogger(__name__)

_RESOURCE_GRAPH_QUERY = """
authorizationresources
| where type == "microsoft.authorization/roledefinitions"
| where properties.type == "BuiltInRole"
| where properties.isServiceRole == false
| project id, properties
"""


async def fetch_builtin_roles() -> list[RoleDefinition]:
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
        logger.exception("Failed to fetch built-in roles")
        raise

    logger.info("Total built-in roles fetched: %d", len(roles))
    return roles
