"""Analytics service."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from azurerbac.analytics.models import AnalyticsData, PermissionChangeStats
from azurerbac.analytics.queries import (
    compute_top_providers,
    compute_top_roles_by_permissions,
    fetch_all_time_stats,
    fetch_daily_changes,
    fetch_frequently_updated_roles,
    fetch_monitoring_health,
    fetch_new_operations_count,
    fetch_operations_summary,
    fetch_permission_change_stats,
    fetch_recent_operations,
    fetch_recently_created_roles,
    fetch_recently_deleted_roles,
    fetch_recently_updated_roles,
    fetch_rolling_stats,
    fetch_volatile_roles,
)
from azurerbac.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from azurerbac.cache.models import CachedRole
    from azurerbac.matching.models import RoleNetPermissions

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Computes and caches analytics data for the dashboard."""

    __slots__ = ("_analytics_data",)

    def __init__(self, analytics_data: AnalyticsData | None = None) -> None:
        self._analytics_data: AnalyticsData = analytics_data or AnalyticsData()

    @property
    def analytics_data(self) -> AnalyticsData:
        return self._analytics_data

    @property
    def is_computed(self) -> bool:
        return self._analytics_data.computed_at is not None

    @property
    def computed_at(self) -> dt.datetime | None:
        return self._analytics_data.computed_at

    async def build_from_db(
        self,
        session: AsyncSession,
        all_ops_lower: set[str] | None = None,
        *,
        roles_by_id: dict[str, CachedRole] | None = None,
        role_net_permissions: dict[str, RoleNetPermissions] | None = None,
    ) -> AnalyticsData:
        """Build analytics from database. Called during cache refresh."""
        logger.info("Building analytics data from database...")
        start_time = dt.datetime.now(dt.UTC)

        # Run queries sequentially (SQLAlchemy async sessions don't support concurrent ops)
        all_time = await fetch_all_time_stats(session)
        logger.debug("Fetched all-time stats: %d scans", all_time.total_scans)

        rolling_30d = await fetch_rolling_stats(session, 30)
        rolling_90d = await fetch_rolling_stats(session, 90)
        daily_changes = await fetch_daily_changes(session)

        frequently_updated = await fetch_frequently_updated_roles(session)
        logger.debug("Fetched %d frequently updated roles", len(frequently_updated))

        recently_created = await fetch_recently_created_roles(session)
        recently_updated = await fetch_recently_updated_roles(session)
        recently_deleted = await fetch_recently_deleted_roles(session)
        volatile_roles = await fetch_volatile_roles(session)
        logger.debug(
            "Recent activity: %d created, %d updated, %d deleted, %d volatile",
            len(recently_created),
            len(recently_updated),
            len(recently_deleted),
            len(volatile_roles),
        )

        # Permission stats require operation names for wildcard expansion
        if all_ops_lower:
            permission_stats = await fetch_permission_change_stats(session, all_ops_lower)
            top_providers = compute_top_providers(all_ops_lower)
        else:
            permission_stats = PermissionChangeStats()
            top_providers = []
            logger.warning(
                "Skipping permission/provider stats: no operations provided for wildcard expansion"
            )

        # Compute top roles by permissions from precomputed cache data
        if roles_by_id and role_net_permissions:
            top_by_actions, top_by_data_actions = compute_top_roles_by_permissions(
                roles_by_id, role_net_permissions
            )
        else:
            top_by_actions, top_by_data_actions = [], []
            logger.warning("Skipping top roles: no role_net_permissions provided")

        new_ops_30d = await fetch_new_operations_count(session)
        recent_ops = await fetch_recent_operations(session)
        total_ops, total_providers = await fetch_operations_summary(session)
        logger.debug(
            "Operations summary: %d total, %d new (30d), %d providers",
            total_ops,
            new_ops_30d,
            total_providers,
        )

        health = await fetch_monitoring_health(session)

        elapsed = (dt.datetime.now(dt.UTC) - start_time).total_seconds()
        logger.info("Analytics data built in %.2f seconds", elapsed)

        self._analytics_data = AnalyticsData(
            all_time=all_time,
            rolling_30d=rolling_30d,
            rolling_90d=rolling_90d,
            daily_changes=daily_changes,
            frequently_updated=frequently_updated,
            recently_created=recently_created,
            recently_updated=recently_updated,
            recently_deleted=recently_deleted,
            volatile_roles=volatile_roles,
            permission_stats=permission_stats,
            top_providers=top_providers,
            top_roles_by_actions=top_by_actions,
            top_roles_by_data_actions=top_by_data_actions,
            new_operations_30d=new_ops_30d,
            recent_operations=recent_ops,
            total_operations=total_ops,
            total_providers=total_providers,
            health=health,
            computed_at=dt.datetime.now(dt.UTC),
        )

        return self._analytics_data

    def swap(self, analytics_data: AnalyticsData) -> None:
        """Swap in new analytics data atomically."""
        self._analytics_data = analytics_data


# Thread-safe singleton
_analytics_service_singleton = ThreadSafeSingleton(AnalyticsService)


def get_analytics_service() -> AnalyticsService:
    """Get or create the analytics service singleton."""
    return _analytics_service_singleton.get()


def reset_analytics_service() -> None:
    """Reset the singleton (for testing only)."""
    _analytics_service_singleton.reset()
