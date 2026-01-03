"""Azure API clients for fetching roles and operations."""

from .operations import fetch_provider_operations
from .roles import fetch_builtin_roles

__all__ = ["fetch_builtin_roles", "fetch_provider_operations"]
