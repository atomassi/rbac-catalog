"""Tests for dashboard behavior.

Focus on service-layer behavior (data filtering/search/pagination) rather than
route wiring or app.state setup, which is exercised by the E2E suite.
"""

import datetime as dt
from datetime import UTC
from unittest.mock import MagicMock, patch

# =============================================================================
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

        result = enrich_role_with_counts(role, app_cache=mock_app_cache)

        # Cache miss returns zeros (no fallback to role_json anymore)
        assert result["actions_count"] == 0
        assert result["data_actions_count"] == 0

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

        result = enrich_role_with_counts(role, app_cache=mock_app_cache)

        assert result["actions_count"] == 5
        assert result["data_actions_count"] == 2


class TestFilterCachedEvents:
    """Tests for filter_cached_events function."""

    def test_filter_all_events_respects_cutoff(self):
        """Test that 'all' returns only recent events across types."""
        from azurerbac.web.services.dashboard import filter_cached_events

        now = dt.datetime.now(UTC)
        cutoff = now - dt.timedelta(days=5)

        cached_events = [
            {
                "role_id": "created_recent",
                "event_type": "created",
                "azure_updated_on": now - dt.timedelta(days=1),
                "scan_timestamp": now - dt.timedelta(days=1),
            },
            {
                "role_id": "updated_recent",
                "event_type": "updated",
                "azure_updated_on": now - dt.timedelta(days=2),
                "scan_timestamp": now - dt.timedelta(days=2),
            },
            {
                "role_id": "deleted_recent",
                "event_type": "deleted",
                "azure_updated_on": now - dt.timedelta(days=20),
                "scan_timestamp": now - dt.timedelta(days=3),
            },
            {
                "role_id": "updated_old",
                "event_type": "updated",
                "azure_updated_on": now - dt.timedelta(days=10),
                "scan_timestamp": now - dt.timedelta(days=10),
            },
        ]

        deps = MagicMock()
        deps.app_cache.get_role_by_id.return_value = {"role_name": "Test Role"}

        result = filter_cached_events(cached_events, deps, cutoff, "all")

        assert {e.role_id for e in result} == {"created_recent", "updated_recent", "deleted_recent"}


class TestSearchRolesInCache:
    """Tests for search_roles_in_cache function."""

    def test_search_by_name(self):
        """Test searching roles by name."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": {"role_id": "id1", "role_name": "Storage Reader", "status": "active"},
            "id2": {
                "role_id": "id2",
                "role_name": "Storage Contributor",
                "status": "active",
            },
            "id3": {"role_id": "id3", "role_name": "Network Admin", "status": "active"},
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = lambda r: {
                "role_id": r.role_id,
                "role_name": r.role_name,
                "status": r.status,
                "actions_count": 0,
                "data_actions_count": 0,
            }

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
            assert all("storage" in r["role_name"].lower() for r in roles)

    def test_search_exact_match(self):
        """Test exact match search."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": {"role_id": "id1", "role_name": "Reader", "status": "active"},
            "id2": {"role_id": "id2", "role_name": "Storage Reader", "status": "active"},
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = lambda r: {
                "role_id": r.role_id,
                "role_name": r.role_name,
                "status": r.status,
                "actions_count": 0,
                "data_actions_count": 0,
            }

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
            assert roles[0]["role_name"] == "Reader"

    def test_search_respects_status_filter(self):
        """Test that status filter is applied."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        cached_roles = {
            "id1": {"role_id": "id1", "role_name": "Test Role 1", "status": "active"},
            "id2": {"role_id": "id2", "role_name": "Test Role 2", "status": "deleted"},
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = lambda r: {
                "role_id": r.role_id,
                "role_name": r.role_name,
                "status": r.status,
                "actions_count": 0,
                "data_actions_count": 0,
            }

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
            assert roles[0]["status"] == "active"

    def test_search_by_role_id(self):
        """Test searching by role ID (GUID)."""
        from azurerbac.web.services.dashboard import search_roles_in_cache

        test_guid = "12345678-1234-1234-1234-123456789abc"
        cached_roles = {
            test_guid: {
                "role_id": test_guid,
                "role_name": "Some Role",
                "status": "active",
            },
            "other-id": {
                "role_id": "other-id",
                "role_name": "Other Role",
                "status": "active",
            },
        }

        with patch("azurerbac.web.services.dashboard.enrich_role_with_counts") as mock_enrich:
            mock_enrich.side_effect = lambda r: {
                "role_id": r.role_id,
                "role_name": r.role_name,
                "status": r.status,
                "actions_count": 0,
                "data_actions_count": 0,
            }

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
            assert roles[0]["role_id"] == test_guid
