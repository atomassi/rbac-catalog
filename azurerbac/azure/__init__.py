"""Azure API clients for fetching roles and operations."""

from .models import OperationData, Permission, RoleDefinition, RoleProperties
from .operations import fetch_provider_operations
from .roles import fetch_builtin_roles

__all__ = [
    "OperationData",
    "Permission",
    "RoleDefinition",
    "RoleProperties",
    "fetch_builtin_roles",
    "fetch_provider_operations",
]
