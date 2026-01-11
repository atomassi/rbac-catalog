"""Shared enums used across multiple modules.

Centralizes common enums (like SortOrder) to avoid circular imports
and provide a single source of truth.
"""

from enum import StrEnum


class SortOrder(StrEnum):
    """Sort order direction - used across all sortable listings."""

    ASC = "asc"
    DESC = "desc"

    @property
    def is_descending(self) -> bool:
        """Check if this is descending order."""
        return self == SortOrder.DESC


class StatusFilter(StrEnum):
    """Status filter for role listings."""

    ACTIVE = "active"
    DELETED = "deleted"
    ALL = "all"


class EventTypeFilter(StrEnum):
    """Event type filter for recent changes."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ALL = "all"
