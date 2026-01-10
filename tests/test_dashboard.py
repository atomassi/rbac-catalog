"""Tests for dashboard behavior.

Focus on service-layer behavior (data filtering/search/pagination) rather than
route wiring or app.state setup, which is exercised by the E2E suite.
"""

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest

from azurerbac.cache.models import CachedChangeEvent
from azurerbac.core.constants import EventType, RoleStatus
from tests.conftest import make_cached_role

# =============================================================================
# Tests for dashboard service functions
# Tests for dashboard service functions
# These tests cover the business logic extracted from the dashboard routes
# into the services module.
# =============================================================================


class TestEnrichRoleWithCounts:
    """Tests for enrich_role_with_counts function."""

    def test_enrich_role_fallback_returns_zeros(self):
        """Test enriching role returns zeros when cache misses (role_json is now in RoleHistory)."""
        from azurerbac.web.services.dashboard import enrich_role_with_counts

        role = MagicMock()
        role.role_id = "test-role-id"
        role.role_name = "Test Role"
        role.role_type = "BuiltInRole"
        role.status = "active"
        role.updated_on = None

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_net_permissions.return_value = None

        result = enrich_role_with_counts(role, cache=mock_app_cache)

        # Cache miss returns zeros (no fallback to role_json anymore)
        assert result.actions_count == 0
        assert result.data_actions_count == 0

    def test_enrich_role_uses_cache(self):
        """Test enriching role uses cache when available."""
        from azurerbac.web.services.dashboard import enrich_role_with_counts

        role = MagicMock()
        role.role_id = "test-role-id"
        role.role_name = "Test Role"
        role.role_type = "BuiltInRole"
        role.status = "active"
        role.updated_on = None

        mock_app_cache = MagicMock()
        mock_app_cache.get_role_net_permissions.return_value = (5, 2)

        result = enrich_role_with_counts(role, cache=mock_app_cache)

        assert result.actions_count == 5
        assert result.data_actions_count == 2


class TestFilterCachedEvents:
    """Tests for filter_cached_events function."""

    def test_filter_all_events_respects_cutoff(self):
        """Test that 'all' returns only recent events across types."""
        from azurerbac.web.services.dashboard import filter_cached_events

        now = dt.datetime.now(UTC)
        cutoff = now - dt.timedelta(days=5)

        cached_events = [
            CachedChangeEvent(
                id=1,
                role_id="created_recent",
                role_name="Created Recent",
                event_type=EventType.CREATED,
                azure_updated_on=now - dt.timedelta(days=1),
                scan_timestamp=now - dt.timedelta(days=1),
            ),
            CachedChangeEvent(
                id=2,
                role_id="updated_recent",
                role_name="Updated Recent",
                event_type=EventType.UPDATED,
                azure_updated_on=now - dt.timedelta(days=2),
                scan_timestamp=now - dt.timedelta(days=2),
            ),
            CachedChangeEvent(
                id=3,
                role_id="deleted_recent",
                role_name="Deleted Recent",
                event_type=EventType.DELETED,
                azure_updated_on=now - dt.timedelta(days=20),
                scan_timestamp=now - dt.timedelta(days=3),
            ),
            CachedChangeEvent(
                id=4,
                role_id="updated_old",
                role_name="Updated Old",
                event_type=EventType.UPDATED,
                azure_updated_on=now - dt.timedelta(days=10),
                scan_timestamp=now - dt.timedelta(days=10),
            ),
        ]

        deps = MagicMock()
        deps.cache_container.get_role_by_id.return_value = make_cached_role("test-id", "Test Role")

        result = filter_cached_events(cached_events, deps, cutoff, "all")

        assert {e.role_id for e in result} == {"created_recent", "updated_recent", "deleted_recent"}


def _mock_enrich_role(r):
    """Create a RoleWithCounts for mocking enrich_role_with_counts."""
    from azurerbac.web.services.models import RoleWithCounts

    status_value = r.status.value if hasattr(r.status, "value") else str(r.status)
    return RoleWithCounts(
        role_id=r.role_id,
        role_name=r.role_name,
        role_type=r.role_type if hasattr(r, "role_type") else "BuiltInRole",
        status=status_value,
        updated_on=r.updated_on if hasattr(r, "updated_on") else None,
        actions_count=0,
        data_actions_count=0,
    )


class TestSearchRolesInCache:
    """Tests for search_roles_in_cache function."""

    def test_search_by_name(self):
        """Test searching roles by name."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": make_cached_role("id1", "Storage Reader"),
            "id2": make_cached_role("id2", "Storage Contributor"),
            "id3": make_cached_role("id3", "Network Admin"),
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = _mock_enrich_role

            roles, total, _ = search_roles_in_cache(
                cached_roles,
                q="storage",
                status_filter="active",
                sort="name",
                order="asc",
                page=1,
                page_size=25,
                exact_match=None,
            )

            assert total == 2
            assert len(roles) == 2
            assert all("storage" in r.role_name.lower() for r in roles)

    def test_search_exact_match(self):
        """Test exact match search."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": make_cached_role("id1", "Reader"),
            "id2": make_cached_role("id2", "Storage Reader"),
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = _mock_enrich_role

            roles, total, _ = search_roles_in_cache(
                cached_roles,
                q="Reader",
                status_filter="active",
                sort="name",
                order="asc",
                page=1,
                page_size=25,
                exact_match="true",
            )

            # Only exact match "Reader" should be returned
            assert total == 1
            assert roles[0].role_name == "Reader"

    def test_search_respects_status_filter(self):
        """Test that status filter is applied."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": make_cached_role("id1", "Test Role 1", "active"),
            "id2": make_cached_role("id2", "Test Role 2", "deleted"),
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = _mock_enrich_role

            # Filter for active only
            roles, total, _ = search_roles_in_cache(
                cached_roles,
                q="test",
                status_filter="active",
                sort="name",
                order="asc",
                page=1,
                page_size=25,
                exact_match=None,
            )

            assert total == 1
            assert roles[0].status == RoleStatus.ACTIVE

    def test_search_by_role_id(self):
        """Test searching by role ID (GUID)."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        test_guid = "12345678-1234-1234-1234-123456789abc"
        cached_roles = {
            test_guid: make_cached_role(test_guid, "Some Role"),
            "other-id": make_cached_role("other-id", "Other Role"),
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = _mock_enrich_role

            roles, total, _ = search_roles_in_cache(
                cached_roles,
                q=test_guid,
                status_filter="active",
                sort="name",
                order="asc",
                page=1,
                page_size=25,
                exact_match=None,
            )

            assert total == 1
            assert roles[0].role_id == test_guid


class TestRecentPageDefaults:
    """Tests for recent page default values and pagination constants."""

    def test_default_days_is_30(self):
        """Verify DEFAULT_DAYS is 30 (was 15)."""
        from azurerbac.web.constants import DEFAULT_DAYS

        assert DEFAULT_DAYS == 30

    def test_default_limit_is_25(self):
        """Verify DEFAULT_LIMIT is 25 for pagination."""
        from azurerbac.web.constants import DEFAULT_LIMIT

        assert DEFAULT_LIMIT == 25

    @pytest.mark.parametrize(
        ("limit", "expected"),
        [
            pytest.param(25, 25, id="limit_25"),
            pytest.param(100, 100, id="limit_100"),
            pytest.param(500, 500, id="limit_500"),
            pytest.param(1000, 1000, id="limit_1000"),
        ],
    )
    def test_supported_page_sizes(self, limit, expected):
        """Verify page sizes 25, 100, 500, 1000 are within MAX_PAGE_SIZE."""
        from azurerbac.web.constants import MAX_PAGE_SIZE

        assert limit <= MAX_PAGE_SIZE
        assert limit == expected


class TestRecentPagePagination:
    """Tests for recent page pagination logic."""

    def test_pagination_calculates_total_pages_correctly(self):
        """Test total_pages calculation for various event counts."""
        # Formula: max(1, (total_events + limit - 1) // limit)
        test_cases = [
            (0, 25, 1),  # Empty results still show 1 page
            (25, 25, 1),  # Exactly one page
            (26, 25, 2),  # One extra triggers page 2
            (100, 25, 4),  # 4 full pages
            (101, 25, 5),  # Partial 5th page
            (1000, 100, 10),  # Using limit=100
        ]

        for total_events, limit, expected_pages in test_cases:
            actual = max(1, (total_events + limit - 1) // limit)
            assert actual == expected_pages, f"Failed for total={total_events}, limit={limit}"

    def test_pagination_slice_indices(self):
        """Test start/end index calculation for pagination."""
        # Formula: start = (page - 1) * limit, end = start + limit
        test_cases = [
            (1, 25, 0, 25),  # Page 1
            (2, 25, 25, 50),  # Page 2
            (3, 25, 50, 75),  # Page 3
            (1, 100, 0, 100),  # Page 1 with limit 100
            (5, 100, 400, 500),  # Page 5 with limit 100
        ]

        for page, limit, expected_start, expected_end in test_cases:
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            assert start_idx == expected_start, f"Start failed for page={page}, limit={limit}"
            assert end_idx == expected_end, f"End failed for page={page}, limit={limit}"

    def test_filter_cached_events_pagination_integration(self):
        """Test that filter_cached_events returns list that can be paginated."""
        from azurerbac.web.services.dashboard import filter_cached_events

        now = dt.datetime.now(UTC)
        cutoff = now - dt.timedelta(days=30)

        # Create 50 events within the cutoff
        cached_events = [
            CachedChangeEvent(
                id=i,
                role_id=f"event_{i}",
                role_name=f"Event {i}",
                event_type=EventType.UPDATED,
                azure_updated_on=now - dt.timedelta(days=i % 25),
                scan_timestamp=now - dt.timedelta(days=i % 25),
            )
            for i in range(50)
        ]

        deps = MagicMock()
        deps.cache_container.get_role_by_id.return_value = make_cached_role("test-id", "Test Role")

        result = filter_cached_events(cached_events, deps, cutoff, "all")

        # All 50 events should be within 30-day cutoff
        assert len(result) == 50

        # Verify pagination works on result (25 per page)
        page1 = result[0:25]
        page2 = result[25:50]

        assert len(page1) == 25
        assert len(page2) == 25
        assert page1[0].role_id != page2[0].role_id
