"""Domain constants for Azure RBAC."""

from enum import StrEnum
from typing import Final


class EventType(StrEnum):
    """Role change event types."""

    CREATED = "created"  # Truly new role (azure created_on == updated_on)
    INITIAL_SCAN = "initial_scan"  # Pre-existing role discovered on first scan
    UPDATED = "updated"
    DELETED = "deleted"


class RoleStatus(StrEnum):
    """Role lifecycle status."""

    ACTIVE = "active"
    DELETED = "deleted"


# High privilege roles that should be flagged with warnings.
HIGH_PRIVILEGE_ROLES: Final[frozenset[str]] = frozenset(
    {
        "Owner",
        "Contributor",
        "User Access Administrator",
        "Role Based Access Control Administrator",
    }
)

# Default limits for search and matching operations
DEFAULT_SEARCH_LIMIT: Final[int] = 50
MAX_UNCOVERED_SAMPLE: Final[int] = 50
