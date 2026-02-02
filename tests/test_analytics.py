"""Unit tests for analytics module."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import pytest

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
from azurerbac.analytics.service import AnalyticsService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_datetime() -> dt.datetime:
    return dt.datetime(2024, 1, 15, 12, 0, 0, tzinfo=dt.UTC)


@pytest.fixture
def sample_date() -> dt.date:
    return dt.date(2024, 1, 15)


# =============================================================================
# Model Round-Trip Tests (Parametrized)
# =============================================================================


class TestModelRoundTrip:
    """Verify to_dict/from_dict round-trips preserve data."""

    @pytest.mark.parametrize(
        ("model_cls", "kwargs"),
        [
            pytest.param(
                AllTimeStats,
                {
                    "total_additions": 50,
                    "total_updates": 30,
                    "total_deletions": 20,
                    "total_scans": 10,
                    "first_scan_date": dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
                    "last_scan_date": dt.datetime(2024, 6, 1, tzinfo=dt.UTC),
                },
                id="AllTimeStats",
            ),
            pytest.param(
                RollingStats,
                {"window_days": 30, "additions": 10, "updates": 5, "deletions": 2},
                id="RollingStats",
            ),
            pytest.param(
                DailyChanges,
                {"date": dt.date(2024, 1, 15), "additions": 5, "updates": 3, "deletions": 1},
                id="DailyChanges",
            ),
            pytest.param(
                FrequentlyUpdatedRole,
                {
                    "role_id": "r1",
                    "role_name": "Test Role",
                    "update_count": 15,
                    "last_updated": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                },
                id="FrequentlyUpdatedRole",
            ),
            pytest.param(
                RecentlyCreatedRole,
                {
                    "role_id": "r1",
                    "role_name": "Test Role",
                    "created_at": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                },
                id="RecentlyCreatedRole",
            ),
            pytest.param(
                RecentlyUpdatedRole,
                {
                    "role_id": "r1",
                    "role_name": "Test Role",
                    "last_updated": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                },
                id="RecentlyUpdatedRole",
            ),
            pytest.param(
                DeletedRole,
                {
                    "role_id": "r1",
                    "role_name": "Test Role",
                    "deleted_at": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                    "lifespan_days": 365,
                },
                id="DeletedRole",
            ),
            pytest.param(
                ProviderStats,
                {"provider": "Microsoft.Storage", "role_count": 10, "operation_count": 500},
                id="ProviderStats",
            ),
            pytest.param(
                RecentOperation,
                {
                    "name": "Microsoft.Storage/read",
                    "display_name": "Read Storage",
                    "provider": "Microsoft.Storage",
                    "first_seen_at": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                },
                id="RecentOperation",
            ),
            pytest.param(
                MonitoringHealth,
                {
                    "last_scan": dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
                    "days_since_last_change": 1,
                    "total_roles_tracked": 850,
                    "active_roles": 800,
                    "deleted_roles": 50,
                },
                id="MonitoringHealth",
            ),
            pytest.param(
                PermissionChangeStats,
                {
                    "total_actions_added": 100,
                    "total_actions_removed": 50,
                    "total_data_actions_added": 30,
                    "total_data_actions_removed": 10,
                    "update_count": 25,
                },
                id="PermissionChangeStats",
            ),
            pytest.param(
                TopRoleByPermissions,
                {
                    "role_id": "r1",
                    "role_name": "Owner",
                    "count": 17611,
                },
                id="TopRoleByPermissions",
            ),
        ],
    )
    def test_round_trip(self, model_cls: type, kwargs: dict) -> None:
        original = model_cls(**kwargs)
        restored = model_cls.from_dict(original.to_dict())
        assert restored == original


class TestModelDefaults:
    """Verify from_dict with empty dict uses sensible defaults."""

    @pytest.mark.parametrize(
        ("model_cls", "expected_attrs"),
        [
            pytest.param(
                AllTimeStats,
                {"total_additions": 0, "total_scans": 0, "first_scan_date": None},
                id="AllTimeStats",
            ),
            pytest.param(
                RollingStats,
                {"window_days": 0, "additions": 0, "updates": 0},
                id="RollingStats",
            ),
            pytest.param(
                PermissionChangeStats,
                {"total_actions_added": 0, "update_count": 0},
                id="PermissionChangeStats",
            ),
        ],
    )
    def test_from_dict_defaults(self, model_cls: type, expected_attrs: dict) -> None:
        instance = model_cls.from_dict({})
        for attr, expected in expected_attrs.items():
            assert getattr(instance, attr) == expected


# =============================================================================
# Computed Property Tests
# =============================================================================


class TestComputedProperties:
    """Test computed properties on models."""

    @pytest.mark.parametrize(
        ("additions", "updates", "deletions", "expected_total"),
        [
            (50, 30, 20, 100),
            (0, 0, 0, 0),
            (100, 0, 0, 100),
        ],
    )
    def test_all_time_stats_total_changes(
        self, additions: int, updates: int, deletions: int, expected_total: int
    ) -> None:
        stats = AllTimeStats(
            total_additions=additions, total_updates=updates, total_deletions=deletions
        )
        assert stats.total_changes == expected_total

    def test_all_time_stats_days_monitoring(self) -> None:
        stats = AllTimeStats(
            first_scan_date=dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
            last_scan_date=dt.datetime(2024, 1, 31, tzinfo=dt.UTC),
        )
        assert stats.days_monitoring == 31

    def test_all_time_stats_days_monitoring_no_dates(self) -> None:
        assert AllTimeStats().days_monitoring == 0

    @pytest.mark.parametrize(
        ("window_days", "additions", "updates", "deletions", "expected_avg"),
        [
            (30, 15, 10, 5, 1.0),
            (10, 10, 0, 0, 1.0),
            (0, 10, 5, 5, 0),  # Edge case: zero window
        ],
    )
    def test_rolling_stats_daily_avg(
        self, window_days: int, additions: int, updates: int, deletions: int, expected_avg: float
    ) -> None:
        stats = RollingStats(
            window_days=window_days, additions=additions, updates=updates, deletions=deletions
        )
        assert stats.daily_avg == pytest.approx(expected_avg)

    @pytest.mark.parametrize(
        ("added", "removed", "expected_net"),
        [
            (100, 40, 60),
            (0, 50, -50),
            (100, 100, 0),
        ],
    )
    def test_permission_stats_net_actions(
        self, added: int, removed: int, expected_net: int
    ) -> None:
        stats = PermissionChangeStats(total_actions_added=added, total_actions_removed=removed)
        assert stats.net_actions_change == expected_net

    @pytest.mark.parametrize(
        ("added", "removed", "expected_net"),
        [
            (50, 10, 40),
            (0, 25, -25),
            (50, 50, 0),
        ],
    )
    def test_permission_stats_net_data_actions(
        self, added: int, removed: int, expected_net: int
    ) -> None:
        stats = PermissionChangeStats(
            total_data_actions_added=added, total_data_actions_removed=removed
        )
        assert stats.net_data_actions_change == expected_net

    @pytest.mark.parametrize(
        ("additions", "updates", "deletions", "expected_total"),
        [
            (10, 5, 2, 17),
            (0, 0, 0, 0),
            (100, 0, 0, 100),
        ],
    )
    def test_rolling_stats_total_changes(
        self, additions: int, updates: int, deletions: int, expected_total: int
    ) -> None:
        stats = RollingStats(additions=additions, updates=updates, deletions=deletions)
        assert stats.total_changes == expected_total

    def test_daily_changes_total(self) -> None:
        dc = DailyChanges(date=dt.date(2024, 1, 15), additions=5, updates=3, deletions=1)
        assert dc.total == 9


# =============================================================================
# AnalyticsData Tests
# =============================================================================


class TestAnalyticsData:
    """Tests for AnalyticsData composite model."""

    def test_defaults(self) -> None:
        data = AnalyticsData()
        assert data.all_time == AllTimeStats()
        assert data.rolling_30d == RollingStats(window_days=30)
        assert data.daily_changes == []
        assert data.computed_at is None

    def test_round_trip(self, sample_datetime: dt.datetime, sample_date: dt.date) -> None:
        original = AnalyticsData(
            all_time=AllTimeStats(total_additions=50),
            rolling_30d=RollingStats(window_days=30, additions=10),
            daily_changes=[DailyChanges(date=sample_date, additions=5)],
            total_operations=5000,
            computed_at=sample_datetime,
        )
        restored = AnalyticsData.from_dict(original.to_dict())

        assert restored.all_time == original.all_time
        assert restored.rolling_30d == original.rolling_30d
        assert restored.daily_changes == original.daily_changes
        assert restored.total_operations == original.total_operations
        assert restored.computed_at == original.computed_at

    def test_from_dict_with_empty_dict(self) -> None:
        """Verify from_dict handles empty input gracefully."""
        data = AnalyticsData.from_dict({})
        assert data.all_time == AllTimeStats()
        assert data.rolling_30d.window_days == 30  # Default window
        assert data.rolling_90d.window_days == 90  # Default window
        assert data.daily_changes == []
        assert data.computed_at is None

    def test_from_dict_with_partial_data(self) -> None:
        """Verify from_dict handles partial nested objects."""
        partial = {
            "all_time": {"total_additions": 10},
            "total_operations": 100,
        }
        data = AnalyticsData.from_dict(partial)
        assert data.all_time.total_additions == 10
        assert data.all_time.total_updates == 0  # Default
        assert data.total_operations == 100
        assert data.frequently_updated == []


# =============================================================================
# Service Tests
# =============================================================================


class TestAnalyticsService:
    """Tests for AnalyticsService."""

    def test_init_no_data(self) -> None:
        service = AnalyticsService()
        assert not service.is_computed
        assert service.computed_at is None

    def test_init_with_data(self, sample_datetime: dt.datetime) -> None:
        data = AnalyticsData(total_operations=5000, computed_at=sample_datetime)
        service = AnalyticsService(analytics_data=data)
        assert service.is_computed
        assert service.analytics_data.total_operations == 5000

    def test_swap(self) -> None:
        service = AnalyticsService()
        new_data = AnalyticsData(total_operations=5000)
        service.swap(new_data)
        assert service.analytics_data.total_operations == 5000

    @pytest.mark.asyncio
    async def test_build_from_db(self) -> None:
        service = AnalyticsService()

        with (
            patch(
                "azurerbac.analytics.service.fetch_all_time_stats",
                new_callable=AsyncMock,
                return_value=AllTimeStats(total_additions=100),
            ),
            patch(
                "azurerbac.analytics.service.fetch_rolling_stats",
                new_callable=AsyncMock,
                return_value=RollingStats(window_days=30, additions=10),
            ),
            patch(
                "azurerbac.analytics.service.fetch_daily_changes",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_frequently_updated_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_recently_created_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_recently_updated_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_recently_deleted_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_volatile_roles",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_permission_change_stats",
                new_callable=AsyncMock,
                return_value=PermissionChangeStats(),
            ),
            patch("azurerbac.analytics.service.compute_top_providers", return_value=[]),
            patch(
                "azurerbac.analytics.service.fetch_new_operations_count",
                new_callable=AsyncMock,
                return_value=0,
            ),
            patch(
                "azurerbac.analytics.service.fetch_recent_operations",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "azurerbac.analytics.service.fetch_operations_summary",
                new_callable=AsyncMock,
                return_value=(5000, 50),
            ),
            patch(
                "azurerbac.analytics.service.fetch_monitoring_health",
                new_callable=AsyncMock,
                return_value=MonitoringHealth(
                    last_scan=None,
                    days_since_last_change=None,
                    total_roles_tracked=0,
                    active_roles=0,
                    deleted_roles=0,
                ),
            ),
        ):
            result = await service.build_from_db(AsyncMock(), {"op1", "op2"})

        assert service.is_computed
        assert result.all_time.total_additions == 100
        assert result.total_operations == 5000


# =============================================================================
# Singleton Tests
# =============================================================================


class TestAnalyticsServiceSingleton:
    """Tests for singleton behavior."""

    def test_get_returns_same_instance(self) -> None:
        from azurerbac.analytics.service import get_analytics_service, reset_analytics_service

        reset_analytics_service()
        service1 = get_analytics_service()
        service2 = get_analytics_service()
        assert service1 is service2

    def test_reset_creates_fresh_instance(self) -> None:
        from azurerbac.analytics.service import get_analytics_service, reset_analytics_service

        service1 = get_analytics_service()
        reset_analytics_service()
        service2 = get_analytics_service()
        assert service1 is not service2


# =============================================================================
# Query Function Tests
# =============================================================================


class TestComputeTopRolesByPermissions:
    """Tests for compute_top_roles_by_permissions function."""

    def test_computes_top_roles_correctly(self) -> None:
        from unittest.mock import MagicMock

        from azurerbac.analytics.queries import compute_top_roles_by_permissions
        from azurerbac.matching.models import RoleNetPermissions

        # Create mock CachedRole objects
        def make_cached_role(role_id: str, role_name: str) -> MagicMock:
            mock = MagicMock()
            mock.role_name = role_name
            return mock

        roles_by_id = {
            "r1": make_cached_role("r1", "Owner"),
            "r2": make_cached_role("r2", "Contributor"),
            "r3": make_cached_role("r3", "Reader"),
        }

        role_net_permissions = {
            "r1": RoleNetPermissions(control_count=1000, data_count=500),
            "r2": RoleNetPermissions(control_count=800, data_count=100),
            "r3": RoleNetPermissions(control_count=50, data_count=0),
        }

        top_by_actions, top_by_data_actions = compute_top_roles_by_permissions(
            roles_by_id, role_net_permissions, limit=10
        )

        # Check control actions ordering
        assert len(top_by_actions) == 3
        assert top_by_actions[0].role_name == "Owner"
        assert top_by_actions[0].count == 1000
        assert top_by_actions[1].role_name == "Contributor"
        assert top_by_actions[2].role_name == "Reader"

        # Check data actions ordering (Reader has 0, so not included)
        assert len(top_by_data_actions) == 2
        assert top_by_data_actions[0].role_name == "Owner"
        assert top_by_data_actions[0].count == 500
        assert top_by_data_actions[1].role_name == "Contributor"

    def test_respects_limit(self) -> None:
        from unittest.mock import MagicMock

        from azurerbac.analytics.queries import compute_top_roles_by_permissions
        from azurerbac.matching.models import RoleNetPermissions

        def make_cached_role(role_id: str, role_name: str) -> MagicMock:
            mock = MagicMock()
            mock.role_name = role_name
            return mock

        # Create 5 roles
        roles_by_id = {f"r{i}": make_cached_role(f"r{i}", f"Role{i}") for i in range(5)}
        role_net_permissions = {
            f"r{i}": RoleNetPermissions(control_count=100 - i * 10, data_count=50 - i * 5)
            for i in range(5)
        }

        top_by_actions, top_by_data_actions = compute_top_roles_by_permissions(
            roles_by_id, role_net_permissions, limit=3
        )

        assert len(top_by_actions) == 3
        assert len(top_by_data_actions) == 3

    def test_handles_empty_input(self) -> None:
        from azurerbac.analytics.queries import compute_top_roles_by_permissions

        top_by_actions, top_by_data_actions = compute_top_roles_by_permissions({}, {})

        assert top_by_actions == []
        assert top_by_data_actions == []


class TestComputeTopProviders:
    """Tests for compute_top_providers pure function."""

    @pytest.mark.parametrize(
        ("all_ops", "limit", "expected_len", "expected_first"),
        [
            pytest.param(
                {
                    "microsoft.storage/storageaccounts/read",
                    "microsoft.storage/storageaccounts/write",
                    "microsoft.compute/virtualmachines/start",
                    "microsoft.compute/virtualmachines/stop",
                    "microsoft.compute/virtualmachines/delete",
                },
                10,
                2,
                ("microsoft.compute", 3),
                id="multi-provider-ops",
            ),
            pytest.param(
                {"provider1/op1", "provider2/op1", "provider2/op2", "provider3/op1"},
                2,
                2,
                ("provider2", 2),
                id="respects-limit",
            ),
            pytest.param(
                set(),
                10,
                0,
                None,
                id="empty-input",
            ),
        ],
    )
    def test_compute_top_providers(
        self,
        all_ops: set[str],
        limit: int,
        expected_len: int,
        expected_first: tuple[str, int] | None,
    ) -> None:
        from azurerbac.analytics.queries import compute_top_providers

        result = compute_top_providers(all_ops, limit=limit)
        assert len(result) == expected_len
        if expected_first:
            assert result[0].provider == expected_first[0]
            assert result[0].operation_count == expected_first[1]

    def test_handles_ops_without_slash(self) -> None:
        from azurerbac.analytics.queries import compute_top_providers

        # Edge case: operations without "/" are treated as provider name itself
        all_ops = {"someop", "microsoft.storage/read"}
        result = compute_top_providers(all_ops)

        assert len(result) == 2
        providers = [r.provider for r in result]
        assert "someop" in providers
        assert "microsoft.storage" in providers


# =============================================================================
# SerializableMixin Tests
# =============================================================================


class TestSerializableMixin:
    """Tests for SerializableMixin edge cases."""

    @pytest.mark.parametrize(
        ("model_cls", "kwargs", "none_field"),
        [
            pytest.param(
                RecentlyCreatedRole,
                {"role_id": "r1", "role_name": "Test", "created_at": None},
                "created_at",
                id="RecentlyCreatedRole-created_at",
            ),
            pytest.param(
                RecentlyUpdatedRole,
                {"role_id": "r1", "role_name": "Test", "last_updated": None},
                "last_updated",
                id="RecentlyUpdatedRole-last_updated",
            ),
            pytest.param(
                DeletedRole,
                {"role_id": "r1", "role_name": "Test", "deleted_at": None, "lifespan_days": None},
                "deleted_at",
                id="DeletedRole-deleted_at",
            ),
        ],
    )
    def test_handles_none_values(self, model_cls: type, kwargs: dict, none_field: str) -> None:
        """Verify None values are preserved in round-trip."""
        original = model_cls(**kwargs)
        restored = model_cls.from_dict(original.to_dict())
        assert getattr(restored, none_field) is None
        assert restored == original

    @pytest.mark.parametrize(
        ("model_cls", "expected_defaults"),
        [
            pytest.param(
                AllTimeStats,
                {"total_additions": 0, "total_scans": 0, "first_scan_date": None},
                id="AllTimeStats",
            ),
            pytest.param(
                RollingStats,
                {"window_days": 0, "additions": 0, "deletions": 0},
                id="RollingStats",
            ),
            pytest.param(
                PermissionChangeStats,
                {"total_actions_added": 0, "update_count": 0},
                id="PermissionChangeStats",
            ),
        ],
    )
    def test_handles_missing_fields_with_defaults(
        self, model_cls: type, expected_defaults: dict
    ) -> None:
        """Verify missing fields use _field_defaults."""
        instance = model_cls.from_dict({})
        for field, expected in expected_defaults.items():
            assert getattr(instance, field) == expected

    @pytest.mark.parametrize(
        ("date_value", "expected_iso"),
        [
            pytest.param(dt.date(2024, 6, 15), "2024-06-15", id="mid-year"),
            pytest.param(dt.date(2024, 1, 1), "2024-01-01", id="year-start"),
            pytest.param(dt.date(2024, 12, 31), "2024-12-31", id="year-end"),
        ],
    )
    def test_date_serialization(self, date_value: dt.date, expected_iso: str) -> None:
        """Verify date fields serialize to ISO format strings."""
        dc = DailyChanges(date=date_value, additions=5, updates=3, deletions=1)
        data = dc.to_dict()
        assert data["date"] == expected_iso


# =============================================================================
# Web Service Tests
# =============================================================================


class TestGetAnalyticsFromCache:
    """Tests for get_analytics_from_cache web service helper."""

    def test_returns_analytics_when_available(self) -> None:
        """Verify analytics are returned when cache is populated."""
        from unittest.mock import MagicMock, patch

        from azurerbac.web.services.analytics import get_analytics_from_cache

        mock_analytics = AnalyticsData(computed_at=dt.datetime.now(dt.UTC))
        mock_cache = MagicMock()
        mock_cache.analytics = mock_analytics
        mock_service = MagicMock()
        mock_service.cache = mock_cache

        with patch("azurerbac.web.services.analytics.get_cache_service", return_value=mock_service):
            result = get_analytics_from_cache()

        assert result is mock_analytics

    def test_raises_503_when_analytics_not_available(self) -> None:
        """Verify HTTPException 503 when cache.analytics is None."""
        from unittest.mock import MagicMock, patch

        from fastapi import HTTPException

        from azurerbac.web.services.analytics import get_analytics_from_cache

        mock_cache = MagicMock()
        mock_cache.analytics = None
        mock_service = MagicMock()
        mock_service.cache = mock_cache

        with (
            patch("azurerbac.web.services.analytics.get_cache_service", return_value=mock_service),
            pytest.raises(HTTPException) as exc_info,
        ):
            get_analytics_from_cache()

        assert exc_info.value.status_code == 503
        assert "not available" in exc_info.value.detail
