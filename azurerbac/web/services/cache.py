"""Cache data access services."""

from __future__ import annotations

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache import get_cache_container


async def get_all_operations() -> list[OperationData]:
    """Get all operations from cache."""
    return get_cache_container().get_all_operations()


async def get_all_roles() -> list[RoleDefinition]:
    """Get all active roles from cache."""
    return get_cache_container().get_all_roles()
