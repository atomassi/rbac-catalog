"""Domain constants for Azure RBAC."""

from enum import StrEnum
from typing import Final

# Domain constants - canonical source for domain references
NEW_DOMAIN: Final[str] = "rbac-catalog.dev"
SITE_DOMAIN: Final[str] = NEW_DOMAIN  # Alias for backwards compatibility
SITE_URL: Final[str] = f"https://{NEW_DOMAIN}"


class EventType(StrEnum):
    """Role change event types."""

    CREATED = "created"
    INITIAL_SCAN = "initial_scan"
    UPDATED = "updated"
    DELETED = "deleted"


class RoleStatus(StrEnum):
    """Role lifecycle status."""

    ACTIVE = "active"
    DELETED = "deleted"


HIGH_PRIVILEGE_ROLES: Final[frozenset[str]] = frozenset(
    {
        "Owner",
        "Contributor",
        "User Access Administrator",
        "Role Based Access Control Administrator",
    }
)

DEFAULT_ROLE_TYPE: Final[str] = "BuiltInRole"
ROLE_DEFINITION_TYPE: Final[str] = "Microsoft.Authorization/roleDefinitions"
DEFAULT_SEARCH_LIMIT: Final[int] = 50
MAX_UNCOVERED_SAMPLE: Final[int] = 50
