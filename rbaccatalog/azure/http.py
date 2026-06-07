from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx

from rbaccatalog.azure.auth import default_azure_credential, get_management_token

MANAGEMENT_BASE_URL: Final = "https://management.azure.com"


def management_url(path: str) -> str:
    """Build full Azure Management API URL."""
    return f"{MANAGEMENT_BASE_URL}{path if path.startswith('/') else '/' + path}"


@asynccontextmanager
async def authenticated_management_async_client(
    *,
    timeout: float,
) -> AsyncIterator[httpx.AsyncClient]:
    """Async context manager for authenticated Azure Management API client."""
    async with default_azure_credential() as credential:
        token = await get_management_token(credential)
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        ) as client:
            yield client
