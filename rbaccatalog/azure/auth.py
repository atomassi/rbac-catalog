from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from azure.identity.aio import DefaultAzureCredential, ManagedIdentityCredential

AZURE_MGMT_SCOPE: Final = "https://management.azure.com/.default"

ManagementCredential = DefaultAzureCredential | ManagedIdentityCredential


@asynccontextmanager
async def default_azure_credential() -> AsyncIterator[ManagementCredential]:
    """Async context manager for the Azure Management API credential.

    When managed identity is enabled, pin a ``ManagedIdentityCredential`` to the
    configured user-assigned identity (``azure_client_id``) so the platform knows
    which UAMI to request a token for; otherwise use the default credential
    chain (env / CLI), keeping local dev working.
    """
    from rbaccatalog.settings import Settings

    settings = Settings.get()
    credential: ManagementCredential = (
        ManagedIdentityCredential(client_id=settings.azure_client_id or None)
        if settings.use_managed_identity
        else DefaultAzureCredential()
    )
    try:
        yield credential
    finally:
        await credential.close()


async def get_management_token(credential: ManagementCredential) -> str:
    """Get access token for Azure Management API."""
    return (await credential.get_token(AZURE_MGMT_SCOPE)).token
