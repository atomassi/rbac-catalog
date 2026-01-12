from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from azure.identity.aio import DefaultAzureCredential

AZURE_MGMT_SCOPE: Final = "https://management.azure.com/.default"


@asynccontextmanager
async def default_azure_credential() -> AsyncIterator[DefaultAzureCredential]:
    """Async context manager for DefaultAzureCredential."""
    credential = DefaultAzureCredential()
    try:
        yield credential
    finally:
        await credential.close()


async def get_management_token(credential: DefaultAzureCredential) -> str:
    """Get access token for Azure Management API."""
    return (await credential.get_token(AZURE_MGMT_SCOPE)).token
