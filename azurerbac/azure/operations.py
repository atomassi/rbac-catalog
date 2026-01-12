"""Fetch Azure provider operations from the Management API."""

from __future__ import annotations

import logging
from typing import Any, Final

from azurerbac.azure.http import authenticated_management_async_client, management_url
from azurerbac.azure.models import OperationData

logger = logging.getLogger(__name__)

_API_VERSION: Final = "2018-01-01-preview"


def _flatten_provider_operations(data: dict[str, Any]) -> list[OperationData]:
    """Flatten Azure providerOperations payload into OperationData models."""
    operations: list[OperationData] = []
    for provider in data.get("value") or []:
        provider_name = provider.get("displayName", "")

        for op in provider.get("operations") or []:
            operations.append(OperationData.from_azure(op, provider_display_name=provider_name))

        for rt in provider.get("resourceTypes") or []:
            rt_name = rt.get("name", "")
            rt_display = rt.get("displayName", "")
            for op in rt.get("operations") or []:
                operations.append(
                    OperationData.from_azure(
                        op,
                        provider_display_name=provider_name,
                        resource_type=rt_name,
                        resource_type_display_name=rt_display,
                    )
                )
    return operations


async def fetch_provider_operations() -> list[OperationData]:
    """Fetch all provider operations from Azure.

    Calls: GET https://management.azure.com/providers/Microsoft.Authorization/providerOperations
       ?api-version=2018-01-01-preview&$expand=resourceTypes
    """
    url = management_url("/providers/Microsoft.Authorization/providerOperations")
    params = {"api-version": _API_VERSION, "$expand": "resourceTypes"}

    try:
        async with authenticated_management_async_client(timeout=300.0) as client:
            logger.info("Fetching provider operations from Azure...")
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            providers = data.get("value") or []
            logger.info("Found %d providers", len(providers))

            operations = _flatten_provider_operations(data)
            logger.info("Total operations extracted: %d", len(operations))
            return operations
    except Exception:
        logger.exception("Failed to fetch provider operations")
        raise
