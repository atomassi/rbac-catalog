"""Analytics data models."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class AllTimeStats:
    """Aggregate statistics across all scans."""

    total_additions: int = 0
    total_updates: int = 0
    total_deletions: int = 0
    total_scans: int = 0
    first_scan_date: dt.datetime | None = None
    last_scan_date: dt.datetime | None = None

    @property
    def total_changes(self) -> int:
        return self.total_additions + self.total_updates + self.total_deletions


@dataclass(frozen=True, slots=True)
class RollingStats:
    """Rolling window statistics (e.g., last 30/90 days)."""

    window_days: int = 0
    additions: int = 0
    updates: int = 0
    deletions: int = 0

    @property
    def total_changes(self) -> int:
        return self.additions + self.updates + self.deletions

    @property
    def daily_avg(self) -> float:
        return self.total_changes / self.window_days if self.window_days > 0 else 0


@dataclass(frozen=True, slots=True)
class DailyChanges:
    """Changes aggregated by day."""

    date: dt.date | str = ""  # May be string depending on DB driver
    additions: int = 0
    updates: int = 0
    deletions: int = 0

    @property
    def total(self) -> int:
        return self.additions + self.updates + self.deletions


@dataclass(frozen=True, slots=True)
class FrequentlyUpdatedRole:
    """Role with update frequency."""

    role_id: str
    role_name: str
    update_count: int
    last_updated: dt.datetime | None


@dataclass(frozen=True, slots=True)
class RecentlyCreatedRole:
    """Recently created role."""

    role_id: str
    role_name: str
    created_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class RecentlyUpdatedRole:
    """Recently updated role (sorted by date)."""

    role_id: str
    role_name: str
    last_updated: dt.datetime | None


@dataclass(frozen=True, slots=True)
class DeletedRole:
    """Deleted role with timeline info."""

    role_id: str
    role_name: str
    deleted_at: dt.datetime | None
    lifespan_days: int | None


@dataclass(frozen=True, slots=True)
class ProviderStats:
    """Statistics by resource provider."""

    provider: str
    role_count: int
    operation_count: int


@dataclass(frozen=True, slots=True)
class TopRoleByPermissions:
    """Role ranked by permission count (actions or data actions)."""

    role_id: str
    role_name: str
    count: int


@dataclass(frozen=True, slots=True)
class RecentOperation:
    """Recently added operation."""

    name: str
    display_name: str | None
    provider: str | None
    first_seen_at: dt.datetime | None


@dataclass(frozen=True, slots=True)
class MonitoringHealth:
    """Monitoring system health metrics."""

    last_scan: dt.datetime | None = None
    days_since_last_change: int | None = None
    total_roles_tracked: int = 0
    active_roles: int = 0
    deleted_roles: int = 0


@dataclass(frozen=True, slots=True)
class PermissionChangeStats:
    """Statistics about permission changes."""

    total_actions_added: int = 0
    total_actions_removed: int = 0
    total_data_actions_added: int = 0
    total_data_actions_removed: int = 0
    update_count: int = 0

    @property
    def net_actions_change(self) -> int:
        return self.total_actions_added - self.total_actions_removed

    @property
    def net_data_actions_change(self) -> int:
        return self.total_data_actions_added - self.total_data_actions_removed


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
    health: MonitoringHealth = field(default_factory=MonitoringHealth)

    # Cache metadata
    computed_at: dt.datetime | None = None
