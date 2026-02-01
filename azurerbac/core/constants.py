"""Domain constants for Azure RBAC."""

from enum import StrEnum
from typing import Final

# Domain constants - canonical source for domain references
NEW_DOMAIN: Final[str] = "rbac-catalog.dev"
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


# Operation that indicates high-privilege (ability to escalate access)
HIGH_PRIVILEGE_OPERATION: Final[str] = "microsoft.authorization/roleassignments/write"

# Well-known high-privilege role IDs (always high-privilege regardless of permissions analysis)
HIGH_PRIVILEGE_ROLE_IDS: Final[frozenset[str]] = frozenset(
    {
        "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",  # Owner
        "b24988ac-6180-42a0-ab88-20f7382dd24c",  # Contributor
        "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",  # User Access Administrator
    }
)

DEFAULT_ROLE_TYPE: Final[str] = "BuiltInRole"
ROLE_DEFINITION_TYPE: Final[str] = "Microsoft.Authorization/roleDefinitions"
DEFAULT_SEARCH_LIMIT: Final[int] = 50
MAX_UNCOVERED_SAMPLE: Final[int] = 50
