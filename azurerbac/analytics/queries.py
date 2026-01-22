"""Analytics database queries."""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from typing import TYPE_CHECKING

from sqlalchemy import and_, case, distinct, func, select

from azurerbac.analytics.constants import (
    DAILY_CHANGES_DAYS,
    TOP_N_PROVIDERS,
    TOP_N_ROLES,
    VOLATILE_THRESHOLD,
)
from azurerbac.analytics.models import (
    AllTimeStats,
    DailyChanges,
    DeletedRole,
    FrequentlyUpdatedRole,
    MonitoringHealth,
    PermissionChangeStats,
    ProviderStats,
    RecentlyCreatedRole,
    RecentlyUpdatedRole,
    RecentOperation,
    RollingStats,
    TopRoleByPermissions,
)
from azurerbac.core.constants import EventType, RoleStatus
from azurerbac.core.models import Operation, Role, RoleHistory, RoleScanStatus
from azurerbac.core.patterns import expand_patterns_to_operations

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.cache.models import CachedRole
    from azurerbac.matching.models import RoleNetPermissions

logger = logging.getLogger(__name__)


async def fetch_all_time_stats(session: AsyncSession) -> AllTimeStats:
    """Fetch aggregate statistics across all scans."""
    scan_result = await session.execute(
        select(
            func.coalesce(func.sum(RoleScanStatus.additions), 0).label("adds"),
            func.coalesce(func.sum(RoleScanStatus.updates), 0).label("updates"),
            func.coalesce(func.sum(RoleScanStatus.deletions), 0).label("deletes"),
            func.count(RoleScanStatus.id).label("scan_count"),
            func.min(RoleScanStatus.scan_timestamp).label("first_scan"),
            func.max(RoleScanStatus.scan_timestamp).label("last_scan"),
        )
    )
    scan_row = scan_result.one()

    return AllTimeStats(
        total_additions=int(scan_row.adds),
        total_updates=int(scan_row.updates),
        total_deletions=int(scan_row.deletes),
        total_scans=int(scan_row.scan_count),
        first_scan_date=scan_row.first_scan,
        last_scan_date=scan_row.last_scan,
    )


async def fetch_rolling_stats(session: AsyncSession, window_days: int) -> RollingStats:
    """Fetch statistics for a rolling time window."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=window_days)

    scan_result = await session.execute(
        select(
            func.coalesce(func.sum(RoleScanStatus.additions), 0).label("adds"),
            func.coalesce(func.sum(RoleScanStatus.updates), 0).label("updates"),
            func.coalesce(func.sum(RoleScanStatus.deletions), 0).label("deletes"),
        ).where(RoleScanStatus.scan_timestamp >= cutoff)
    )
    scan_row = scan_result.one()

    stats = RollingStats(
        window_days=window_days,
        additions=int(scan_row.adds),
        updates=int(scan_row.updates),
        deletions=int(scan_row.deletes),
    )
    logger.debug(
        "Rolling %dd stats: +%d adds, ~%d updates, -%d deletions",
        window_days,
        stats.additions,
        stats.updates,
        stats.deletions,
    )
    return stats


async def fetch_daily_changes(
    session: AsyncSession,
    days: int = DAILY_CHANGES_DAYS,
) -> list[DailyChanges]:
    """Fetch daily change counts for chart visualization."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    scan_result = await session.execute(
        select(
            func.date(RoleScanStatus.scan_timestamp).label("scan_date"),
            func.sum(RoleScanStatus.additions).label("adds"),
            func.sum(RoleScanStatus.updates).label("updates"),
            func.sum(RoleScanStatus.deletions).label("deletes"),
        )
        .where(RoleScanStatus.scan_timestamp >= cutoff)
        .group_by(func.date(RoleScanStatus.scan_timestamp))
        .order_by(func.date(RoleScanStatus.scan_timestamp))
    )

    daily_list = [
        DailyChanges(
            date=str(row.scan_date) if row.scan_date else "",
            additions=int(row.adds or 0),
            updates=int(row.updates or 0),
            deletions=int(row.deletes or 0),
        )
        for row in scan_result.all()
        if row.scan_date
    ]
    logger.debug("Daily changes: %d days with activity", len(daily_list))
    return daily_list


async def fetch_frequently_updated_roles(
    session: AsyncSession,
    limit: int = TOP_N_ROLES,
    min_updates: int = 2,
) -> list[FrequentlyUpdatedRole]:
    """Fetch roles with the most update events (minimum min_updates)."""
    result = await session.execute(
        select(
            RoleHistory.role_id,
            Role.role_name,
            func.count(RoleHistory.id).label("update_count"),
            func.max(RoleHistory.azure_updated_on).label("last_updated"),
        )
        .join(Role, Role.role_id == RoleHistory.role_id)
        .where(RoleHistory.event_type == EventType.UPDATED)
        .group_by(RoleHistory.role_id, Role.role_name)
        .having(func.count(RoleHistory.id) >= min_updates)
        .order_by(func.count(RoleHistory.id).desc())
        .limit(limit)
    )

    return [
        FrequentlyUpdatedRole(
            role_id=row.role_id,
            role_name=row.role_name,
            update_count=int(row.update_count),
            last_updated=row.last_updated,
        )
        for row in result.all()
    ]


async def fetch_recently_updated_roles(
    session: AsyncSession,
    limit: int = TOP_N_ROLES,
) -> list[RecentlyUpdatedRole]:
    """Fetch most recently updated roles (sorted by azure_updated_on)."""
    result = await session.execute(
        select(
            RoleHistory.role_id,
            Role.role_name,
            RoleHistory.azure_updated_on.label("last_updated"),
        )
        .join(Role, Role.role_id == RoleHistory.role_id)
        .where(RoleHistory.event_type == EventType.UPDATED)
        .where(RoleHistory.azure_updated_on.isnot(None))
        .order_by(RoleHistory.azure_updated_on.desc())
        .limit(limit)
    )

    return [
        RecentlyUpdatedRole(
            role_id=row.role_id,
            role_name=row.role_name,
            last_updated=row.last_updated,
        )
        for row in result.all()
    ]


async def fetch_recently_created_roles(
    session: AsyncSession,
    limit: int = TOP_N_ROLES,
) -> list[RecentlyCreatedRole]:
    """Fetch most recently created roles."""
    result = await session.execute(
        select(
            RoleHistory.role_id,
            RoleHistory.role_name,
            RoleScanStatus.scan_timestamp.label("created_at"),
        )
        .join(RoleScanStatus, RoleScanStatus.id == RoleHistory.scan_id)
        .where(RoleHistory.event_type == EventType.CREATED)
        .order_by(RoleScanStatus.scan_timestamp.desc())
        .limit(limit)
    )

    return [
        RecentlyCreatedRole(
            role_id=row.role_id,
            role_name=row.role_name,
            created_at=row.created_at,
        )
        for row in result.all()
    ]


async def fetch_recently_deleted_roles(
    session: AsyncSession,
    limit: int = TOP_N_ROLES,
) -> list[DeletedRole]:
    """Fetch most recently deleted roles with lifespan calculation."""
    first_seen_sq = (
        select(
            RoleHistory.role_id,
            func.min(RoleScanStatus.scan_timestamp).label("first_seen"),
        )
        .join(RoleScanStatus, RoleScanStatus.id == RoleHistory.scan_id)
        .group_by(RoleHistory.role_id)
        .subquery()
    )

    result = await session.execute(
        select(
            RoleHistory.role_id,
            RoleHistory.role_name,
            RoleScanStatus.scan_timestamp.label("deleted_at"),
            first_seen_sq.c.first_seen,
        )
        .join(RoleScanStatus, RoleScanStatus.id == RoleHistory.scan_id)
        .outerjoin(first_seen_sq, first_seen_sq.c.role_id == RoleHistory.role_id)
        .where(RoleHistory.event_type == EventType.DELETED)
        .order_by(RoleScanStatus.scan_timestamp.desc())
        .limit(limit)
    )

    deleted_roles = []
    for row in result.all():
        lifespan = None
        if row.first_seen and row.deleted_at:
            lifespan = (row.deleted_at - row.first_seen).days
        deleted_roles.append(
            DeletedRole(
                role_id=row.role_id,
                role_name=row.role_name,
                deleted_at=row.deleted_at,
                lifespan_days=lifespan,
            )
        )
    return deleted_roles


async def fetch_volatile_roles(
    session: AsyncSession,
    threshold: int = VOLATILE_THRESHOLD,
    days: int = 90,
    limit: int = TOP_N_ROLES,
) -> list[FrequentlyUpdatedRole]:
    """Fetch roles with frequent updates in recent period (volatile)."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)

    result = await session.execute(
        select(
            RoleHistory.role_id,
            Role.role_name,
            func.count(RoleHistory.id).label("update_count"),
            func.max(RoleHistory.azure_updated_on).label("last_updated"),
        )
        .join(Role, Role.role_id == RoleHistory.role_id)
        .join(RoleScanStatus, RoleScanStatus.id == RoleHistory.scan_id)
        .where(
            and_(
                RoleHistory.event_type == EventType.UPDATED,
                RoleScanStatus.scan_timestamp >= cutoff,
            )
        )
        .group_by(RoleHistory.role_id, Role.role_name)
        .having(func.count(RoleHistory.id) >= threshold)
        .order_by(func.count(RoleHistory.id).desc())
        .limit(limit)
    )

    return [
        FrequentlyUpdatedRole(
            role_id=row.role_id,
            role_name=row.role_name,
            update_count=int(row.update_count),
            last_updated=row.last_updated,
        )
        for row in result.all()
    ]


async def fetch_permission_change_stats(
    session: AsyncSession,
    all_ops_lower: set[str],
) -> PermissionChangeStats:
    """Analyze permission changes from diff_json, counting actions added/removed."""
    result = await session.execute(
        select(RoleHistory.diff_json).where(
            and_(
                RoleHistory.event_type == EventType.UPDATED,
                RoleHistory.diff_json.isnot(None),
            )
        )
    )

    actions_added = 0
    actions_removed = 0
    data_actions_added = 0
    data_actions_removed = 0
    update_count = 0

    for (diff_json,) in result.all():
        if not diff_json:
            continue

        changes = diff_json.get("changes", [])
        for change in changes:
            if change.get("path") != "properties.permissions":
                continue

            update_count += 1
            from_perms = change.get("from", [])
            to_perms = change.get("to", [])

            from_actions: list[str] = []
            from_data_actions: list[str] = []
            to_actions: list[str] = []
            to_data_actions: list[str] = []

            for perm in from_perms if isinstance(from_perms, list) else []:
                if isinstance(perm, dict):
                    from_actions.extend(perm.get("actions", []))
                    from_data_actions.extend(perm.get("dataActions", []))

            for perm in to_perms if isinstance(to_perms, list) else []:
                if isinstance(perm, dict):
                    to_actions.extend(perm.get("actions", []))
                    to_data_actions.extend(perm.get("dataActions", []))

            from_actions_expanded = expand_patterns_to_operations(from_actions, all_ops_lower)
            to_actions_expanded = expand_patterns_to_operations(to_actions, all_ops_lower)
            from_data_expanded = expand_patterns_to_operations(from_data_actions, all_ops_lower)
            to_data_expanded = expand_patterns_to_operations(to_data_actions, all_ops_lower)

            actions_added += len(to_actions_expanded - from_actions_expanded)
            actions_removed += len(from_actions_expanded - to_actions_expanded)
            data_actions_added += len(to_data_expanded - from_data_expanded)
            data_actions_removed += len(from_data_expanded - to_data_expanded)

    return PermissionChangeStats(
        total_actions_added=actions_added,
        total_actions_removed=actions_removed,
        total_data_actions_added=data_actions_added,
        total_data_actions_removed=data_actions_removed,
        update_count=update_count,
    )


def compute_top_providers(
    all_ops_lower: set[str],
    limit: int = TOP_N_PROVIDERS,
) -> list[ProviderStats]:
    """Compute providers with the most operations from cached operation names."""
    provider_counts: Counter[str] = Counter()
    for op_name in all_ops_lower:
        if "/" in op_name:
            provider = op_name.split("/", 1)[0]
            provider_counts[provider] += 1
        elif op_name:
            provider_counts[op_name] += 1

    result = [
        ProviderStats(
            provider=provider or "Unknown",
            role_count=0,
            operation_count=count,
        )
        for provider, count in provider_counts.most_common(limit)
    ]
    logger.debug(
        "Top providers: %d providers from %d total operations",
        len(result),
        len(all_ops_lower),
    )
    return result


async def fetch_new_operations_count(
    session: AsyncSession,
    days: int = 30,
) -> int:
    """Count operations first seen in the last N days."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    result = await session.scalar(
        select(func.count(Operation.name)).where(Operation.first_seen_at >= cutoff)
    )
    return int(result or 0)


async def fetch_recent_operations(
    session: AsyncSession,
    days: int = 30,
) -> list[RecentOperation]:
    """Fetch all recently added operations (first seen in the last N days)."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    result = await session.execute(
        select(
            Operation.name,
            Operation.display_name,
            Operation.provider_display_name,
            Operation.first_seen_at,
        )
        .where(Operation.first_seen_at >= cutoff)
        .order_by(Operation.first_seen_at.desc())
    )
    return [
        RecentOperation(
            name=row.name,
            display_name=row.display_name,
            provider=row.provider_display_name,
            first_seen_at=row.first_seen_at,
        )
        for row in result.all()
    ]


async def fetch_operations_summary(session: AsyncSession) -> tuple[int, int]:
    """Get total operations and unique providers count."""
    result = await session.execute(
        select(
            func.count(Operation.name).label("total_ops"),
            func.count(distinct(Operation.provider_display_name)).label("provider_count"),
        )
    )
    row = result.one()
    return int(row.total_ops or 0), int(row.provider_count or 0)


async def fetch_monitoring_health(session: AsyncSession) -> MonitoringHealth:
    """Fetch monitoring system health metrics."""
    scan_result = await session.execute(
        select(
            func.max(RoleScanStatus.scan_timestamp).label("last_scan"),
        )
    )
    scan_row = scan_result.one()

    last_change = await session.scalar(
        select(func.max(RoleScanStatus.scan_timestamp)).where(
            (RoleScanStatus.additions > 0)
            | (RoleScanStatus.updates > 0)
            | (RoleScanStatus.deletions > 0)
        )
    )
    days_since_last_change = None
    if last_change:
        now = dt.datetime.now(dt.UTC)
        if last_change.tzinfo is None:
            last_change = last_change.replace(tzinfo=dt.UTC)
        days_since_last_change = (now - last_change).days

    role_result = await session.execute(
        select(
            func.count(Role.role_id).label("total"),
            func.sum(case((Role.status == RoleStatus.ACTIVE, 1), else_=0)).label("active"),
            func.sum(case((Role.status == RoleStatus.DELETED, 1), else_=0)).label("deleted"),
        )
    )
    role_row = role_result.one()

    return MonitoringHealth(
        last_scan=scan_row.last_scan,
        days_since_last_change=days_since_last_change,
        total_roles_tracked=int(role_row.total or 0),
        active_roles=int(role_row.active or 0),
        deleted_roles=int(role_row.deleted or 0),
    )


def compute_top_roles_by_permissions(
    roles_by_id: dict[str, CachedRole],
    role_net_permissions: dict[str, RoleNetPermissions],
    limit: int = TOP_N_ROLES,
) -> tuple[list[TopRoleByPermissions], list[TopRoleByPermissions]]:
    """Compute roles with the most allowed actions and data actions.

    Uses precomputed role_net_permissions from cache (already expanded, exclusions applied).

    Returns:
        Tuple of (top_by_actions, top_by_data_actions) lists.
    """
    action_counts: list[tuple[str, str, int]] = []
    data_action_counts: list[tuple[str, str, int]] = []

    for role_id, net_perms in role_net_permissions.items():
        cached_role = roles_by_id.get(role_id)
        if not cached_role:
            continue
        role_name = cached_role.role_name

        if net_perms.control_count > 0:
            action_counts.append((role_id, role_name, net_perms.control_count))
        if net_perms.data_count > 0:
            data_action_counts.append((role_id, role_name, net_perms.data_count))

    action_counts.sort(key=lambda x: x[2], reverse=True)
    data_action_counts.sort(key=lambda x: x[2], reverse=True)

    top_by_actions = [
        TopRoleByPermissions(role_id=rid, role_name=rname, count=cnt)
        for rid, rname, cnt in action_counts[:limit]
    ]
    top_by_data_actions = [
        TopRoleByPermissions(role_id=rid, role_name=rname, count=cnt)
        for rid, rname, cnt in data_action_counts[:limit]
    ]

    logger.debug(
        "Top roles by permissions: %d by actions, %d by data actions",
        len(top_by_actions),
        len(top_by_data_actions),
    )

    return top_by_actions, top_by_data_actions
