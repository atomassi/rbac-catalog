from __future__ import annotations

import logging
from typing import Any

from azurerbac.azure.http import authenticated_management_async_client, management_url
from azurerbac.azure.models import RoleDefinition


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
                    role = RoleDefinition.from_resource_graph(item).to_dict()
                    roles.append(role)

                # Check for pagination
                skip_token = data.get("$skipToken")
                if not skip_token:
                    break

    except Exception as e:
        logger.exception("Failed to fetch built-in roles via Resource Graph: %s", e)
        raise

    logger.info("Total built-in roles fetched: %d", len(roles))

    return roles
