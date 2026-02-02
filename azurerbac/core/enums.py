"""Shared enums across modules."""

from enum import StrEnum


class SortOrder(StrEnum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"


class StatusFilter(StrEnum):
    """Role status filter."""

    ACTIVE = "active"
    DELETED = "deleted"
    ALL = "all"


class EventTypeFilter(StrEnum):
    """Event type filter."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ALL = "all"
