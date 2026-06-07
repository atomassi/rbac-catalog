"""Shared enums across modules."""

from enum import StrEnum


class SortOrder(StrEnum):
    """Sort direction."""

    ASC = "asc"
    DESC = "desc"


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


class StatusFilter(StrEnum):
    """Role status filter (adds an ``ALL`` sentinel over ``RoleStatus``)."""

    ACTIVE = RoleStatus.ACTIVE.value
    DELETED = RoleStatus.DELETED.value
    ALL = "all"


class EventTypeFilter(StrEnum):
    """Event type filter (adds an ``ALL`` sentinel over ``EventType``)."""

    CREATED = EventType.CREATED.value
    UPDATED = EventType.UPDATED.value
    DELETED = EventType.DELETED.value
    ALL = "all"
