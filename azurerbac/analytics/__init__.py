"""Analytics module for Azure RBAC."""

from azurerbac.analytics.models import (
    AllTimeStats,
    AnalyticsData,
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
from azurerbac.analytics.serialization import SerializableMixin
from azurerbac.analytics.service import AnalyticsService

__all__ = [
    "AllTimeStats",
    "AnalyticsData",
    "AnalyticsService",
    "DailyChanges",
    "DeletedRole",
    "FrequentlyUpdatedRole",
    "MonitoringHealth",
    "PermissionChangeStats",
    "ProviderStats",
    "RecentOperation",
    "RecentlyCreatedRole",
    "RecentlyUpdatedRole",
    "RollingStats",
    "SerializableMixin",
    "TopRoleByPermissions",
]
