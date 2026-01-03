from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx

from azurerbac.azure.auth import default_azure_credential, get_management_token

MANAGEMENT_BASE_URL: Final = "https://management.azure.com"


def management_url(path: str) -> str:
    """Build a full Azure Management API URL from a path."""
    if not path.startswith("/"):
        path = "/" + path
    return f"{MANAGEMENT_BASE_URL}{path}"


def management_headers(token: str) -> dict[str, str]:
    """Build HTTP headers for Azure Management API requests."""
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@asynccontextmanager
async def management_async_client(
    token: str,
    *,
    timeout: float,
) -> AsyncIterator[httpx.AsyncClient]:
    """Context manager for an authenticated Azure Management API client."""
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=management_headers(token),
    ) as client:
        yield client


@asynccontextmanager
async def authenticated_management_async_client(
    *,
    timeout: float,
) -> AsyncIterator[httpx.AsyncClient]:
    """Context manager that handles Azure auth and provides an API client."""
    async with default_azure_credential() as credential:
        token = await get_management_token(credential)
        async with management_async_client(token, timeout=timeout) as client:
            yield client
