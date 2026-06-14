from __future__ import annotations

import logging

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from rbaccatalog.azure.http import (
    authenticated_management_async_client,
    is_retryable_azure_error,
    management_url,
)
from rbaccatalog.azure.models import RoleDefinition

logger = logging.getLogger(__name__)

# RBAC API constants
_RBAC_API_VERSION = "2022-05-01-preview"
_RBAC_FILTER = "type eq 'BuiltInRole'"

# Azure response headers
_HEADER_CORRELATION_ID = "x-ms-correlation-request-id"
_HEADER_REQUEST_ID = "x-ms-request-id"


@retry(
    retry=retry_if_exception(is_retryable_azure_error),
    stop=stop_after_attempt(4),
    wait=wait_random_exponential(multiplier=1, max=10),
    reraise=True,
)
async def fetch_builtin_roles() -> list[RoleDefinition]:
    """Fetch all built-in role definitions via the Azure RBAC API.

    Uses the tenant-scoped RBAC API endpoint:
    ``GET /providers/Microsoft.Authorization/roleDefinitions?$filter=type eq 'BuiltInRole'``

    This endpoint does not require Reader access on a subscription. The caller
    still needs ``Microsoft.Authorization/roleDefinitions/read`` at some scope,
    which is included in the default permissions granted to authenticated
    tenant users in most Entra ID tenants.

    Retries on transient failures (network errors, 408, 429, 5xx) up to 4 times with
    exponential backoff + jitter. Non-transient errors (401, 403, 404) are raised immediately.
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

                # Log useful Azure headers for debugging
                headers = response.headers
                logger.info(
                    "RBAC API: status=%d, correlationId=%s, requestId=%s",
                    response.status_code,
                    headers.get(_HEADER_CORRELATION_ID, "N/A"),
                    headers.get(_HEADER_REQUEST_ID, "N/A"),
                )

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
