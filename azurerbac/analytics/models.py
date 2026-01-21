"""Analytics data models.

All models use SerializableMixin for automatic to_dict/from_dict serialization.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import ClassVar

from azurerbac.analytics.serialization import SerializableMixin
from azurerbac.core.types import JsonDict
from azurerbac.core.utils import format_datetime, parse_datetime


@dataclass(frozen=True, slots=True)
class AllTimeStats(SerializableMixin):
    """Aggregate statistics across all scans."""

    total_additions: int = 0
    total_updates: int = 0
    total_deletions: int = 0
    total_scans: int = 0
    first_scan_date: dt.datetime | None = None
    last_scan_date: dt.datetime | None = None

    _field_defaults: ClassVar[dict] = {
        "total_additions": 0,
        "total_updates": 0,
        "total_deletions": 0,
        "total_scans": 0,
    }

    @property
    def total_changes(self) -> int:
        return self.total_additions + self.total_updates + self.total_deletions

    @property
    def days_monitoring(self) -> int:
        if self.first_scan_date and self.last_scan_date:
            return (self.last_scan_date - self.first_scan_date).days + 1
        return 0


@dataclass(frozen=True, slots=True)
class RollingStats(SerializableMixin):
    """Rolling window statistics (e.g., last 30/90 days)."""

    window_days: int = 0
    additions: int = 0
    updates: int = 0
    deletions: int = 0

    _field_defaults: ClassVar[dict] = {
        "window_days": 0,
        "additions": 0,
        "updates": 0,
        "deletions": 0,
    }

    @property
    def total_changes(self) -> int:
        return self.additions + self.updates + self.deletions

    @property
    def daily_avg(self) -> float:
        return self.total_changes / self.window_days if self.window_days > 0 else 0


@dataclass(frozen=True, slots=True)
class DailyChanges(SerializableMixin):
    """Changes aggregated by day."""

    date: dt.date | str = ""  # May be string depending on DB driver
    additions: int = 0
    updates: int = 0
    deletions: int = 0

    _field_defaults: ClassVar[dict] = {"additions": 0, "updates": 0, "deletions": 0}

    @property
    def total(self) -> int:
        return self.additions + self.updates + self.deletions


@dataclass(frozen=True, slots=True)
class FrequentlyUpdatedRole(SerializableMixin):
    """Role with update frequency."""

    role_id: str
    role_name: str
    update_count: int
    last_updated: dt.datetime | None

    _field_defaults: ClassVar[dict] = {"role_id": "", "role_name": "", "update_count": 0}


@dataclass(frozen=True, slots=True)
class RecentlyCreatedRole(SerializableMixin):
    """Recently created role."""

    role_id: str
    role_name: str
    created_at: dt.datetime | None

    _field_defaults: ClassVar[dict] = {"role_id": "", "role_name": ""}


@dataclass(frozen=True, slots=True)
class RecentlyUpdatedRole(SerializableMixin):
    """Recently updated role (sorted by date)."""

    role_id: str
    role_name: str
    last_updated: dt.datetime | None

    _field_defaults: ClassVar[dict] = {"role_id": "", "role_name": ""}


@dataclass(frozen=True, slots=True)
class DeletedRole(SerializableMixin):
    """Deleted role with timeline info."""

    role_id: str
    role_name: str
    deleted_at: dt.datetime | None
    lifespan_days: int | None

    _field_defaults: ClassVar[dict] = {"role_id": "", "role_name": ""}


@dataclass(frozen=True, slots=True)
class ProviderStats(SerializableMixin):
    """Statistics by resource provider."""

    provider: str
    role_count: int
    operation_count: int

    _field_defaults: ClassVar[dict] = {"provider": "", "role_count": 0, "operation_count": 0}


@dataclass(frozen=True, slots=True)
class TopRoleByPermissions(SerializableMixin):
    """Role ranked by permission count (actions or data actions)."""

    role_id: str
    role_name: str
    count: int

    _field_defaults: ClassVar[dict] = {"role_id": "", "role_name": "", "count": 0}


@dataclass(frozen=True, slots=True)
class RecentOperation(SerializableMixin):
    """Recently added operation."""

    name: str
    display_name: str | None
    provider: str | None
    first_seen_at: dt.datetime | None

    _field_defaults: ClassVar[dict] = {"name": ""}


@dataclass(frozen=True, slots=True)
class MonitoringHealth(SerializableMixin):
    """Monitoring system health metrics."""

    last_scan: dt.datetime | None = None
    days_since_last_change: int | None = None
    total_roles_tracked: int = 0
    active_roles: int = 0
    deleted_roles: int = 0

    _field_defaults: ClassVar[dict] = {
        "total_roles_tracked": 0,
        "active_roles": 0,
        "deleted_roles": 0,
    }


@dataclass(frozen=True, slots=True)
class PermissionChangeStats(SerializableMixin):
    """Statistics about permission changes."""

    total_actions_added: int = 0
    total_actions_removed: int = 0
    total_data_actions_added: int = 0
    total_data_actions_removed: int = 0
    update_count: int = 0

    _field_defaults: ClassVar[dict] = {
        "total_actions_added": 0,
        "total_actions_removed": 0,
        "total_data_actions_added": 0,
        "total_data_actions_removed": 0,
        "update_count": 0,
    }

    @property
    def net_actions_change(self) -> int:
        return self.total_actions_added - self.total_actions_removed

    @property
    def net_data_actions_change(self) -> int:
        return self.total_data_actions_added - self.total_data_actions_removed


# Type mapping for nested dataclass lists in AnalyticsData
_NESTED_LIST_TYPES: dict[str, type[SerializableMixin]] = {
    "daily_changes": DailyChanges,
    "frequently_updated": FrequentlyUpdatedRole,
    "recently_created": RecentlyCreatedRole,
    "recently_updated": RecentlyUpdatedRole,
    "recently_deleted": DeletedRole,
    "volatile_roles": FrequentlyUpdatedRole,
    "top_providers": ProviderStats,
    "top_roles_by_actions": TopRoleByPermissions,
    "top_roles_by_data_actions": TopRoleByPermissions,
    "recent_operations": RecentOperation,
}

_NESTED_OBJECT_TYPES: dict[str, type[SerializableMixin]] = {
    "all_time": AllTimeStats,
    "rolling_30d": RollingStats,
    "rolling_90d": RollingStats,
    "permission_stats": PermissionChangeStats,
    "health": MonitoringHealth,
}


@dataclass(slots=True)
class AnalyticsData:
    """Complete analytics data for the dashboard, serializable for caching."""

    # All-time statistics
    all_time: AllTimeStats = field(default_factory=AllTimeStats)

    # Rolling window stats
    rolling_30d: RollingStats = field(default_factory=lambda: RollingStats(window_days=30))
    rolling_90d: RollingStats = field(default_factory=lambda: RollingStats(window_days=90))

    # Time series data
    daily_changes: list[DailyChanges] = field(default_factory=list)

    # Role lifecycle insights
    frequently_updated: list[FrequentlyUpdatedRole] = field(default_factory=list)
    recently_created: list[RecentlyCreatedRole] = field(default_factory=list)
    recently_updated: list[RecentlyUpdatedRole] = field(default_factory=list)
    recently_deleted: list[DeletedRole] = field(default_factory=list)
    volatile_roles: list[FrequentlyUpdatedRole] = field(default_factory=list)

    # Permission changes
    permission_stats: PermissionChangeStats = field(default_factory=PermissionChangeStats)

    # Provider breakdown
    top_providers: list[ProviderStats] = field(default_factory=list)

    # Top roles by permission count (expanded, after exclusions)
    top_roles_by_actions: list[TopRoleByPermissions] = field(default_factory=list)
    top_roles_by_data_actions: list[TopRoleByPermissions] = field(default_factory=list)

    # Operations insights
    new_operations_30d: int = 0
    total_operations: int = 0
    total_providers: int = 0
    recent_operations: list[RecentOperation] = field(default_factory=list)

    # Monitoring health
    health: MonitoringHealth = field(
        default_factory=lambda: MonitoringHealth(
            last_scan=None,
            days_since_last_change=None,
            total_roles_tracked=0,
            active_roles=0,
            deleted_roles=0,
        )
    )

    # Cache metadata
    computed_at: dt.datetime | None = None

    def to_dict(self) -> JsonDict:
        """Serialize to dictionary for cache storage."""
        result: JsonDict = {}

        # Nested objects
        for field_name in _NESTED_OBJECT_TYPES:
            obj = getattr(self, field_name)
            result[field_name] = obj.to_dict()

        # Nested lists
        for field_name in _NESTED_LIST_TYPES:
            items = getattr(self, field_name)
            result[field_name] = [item.to_dict() for item in items]

        # Scalar fields
        result["new_operations_30d"] = self.new_operations_30d
        result["total_operations"] = self.total_operations
        result["total_providers"] = self.total_providers
        result["computed_at"] = format_datetime(self.computed_at)

        return result

    @classmethod
    def from_dict(cls, data: JsonDict) -> AnalyticsData:
        """Deserialize from dictionary (cache retrieval)."""
        kwargs: dict = {}

        # Nested objects
        for field_name, field_type in _NESTED_OBJECT_TYPES.items():
            raw = data.get(field_name, {})
            # Handle RollingStats window_days defaults
            if field_type is RollingStats and "window_days" not in raw:
                raw["window_days"] = 30 if field_name == "rolling_30d" else 90
            kwargs[field_name] = field_type.from_dict(raw)

        # Nested lists
        for field_name, item_type in _NESTED_LIST_TYPES.items():
            raw_list = data.get(field_name, [])
            kwargs[field_name] = [item_type.from_dict(item) for item in raw_list]

        # Scalar fields
        kwargs["new_operations_30d"] = data.get("new_operations_30d", 0)
        kwargs["total_operations"] = data.get("total_operations", 0)
        kwargs["total_providers"] = data.get("total_providers", 0)
        kwargs["computed_at"] = parse_datetime(data.get("computed_at"))

        return cls(**kwargs)
