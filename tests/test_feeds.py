"""Tests for RSS/Atom feed endpoints."""

# ruff: noqa: S314 - XML parsing is safe here, we're parsing our own test-generated XML

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock
from xml.etree import ElementTree as ET

import pytest

from azurerbac.cache.models import CachedChangeEvent
from azurerbac.core.constants import EventType
from azurerbac.web.services.feeds import (
    _build_rich_content,
    build_atom_feed,
    build_rss_feed,
    get_recent_events,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_events() -> list[CachedChangeEvent]:
    """Create sample change events for testing."""
    now = dt.datetime.now(dt.UTC)
    return [
        CachedChangeEvent(
            id=1,
            role_id="role-1",
            role_name="Test Reader Role",
            event_type="created",
            scan_timestamp=now - dt.timedelta(days=1),
            azure_updated_on=now - dt.timedelta(days=1),
            summary="Role created",
        ),
        CachedChangeEvent(
            id=2,
            role_id="role-2",
            role_name="Test Writer Role",
            event_type="updated",
            scan_timestamp=now - dt.timedelta(days=5),
            azure_updated_on=now - dt.timedelta(days=5),
            summary="Permissions updated",
        ),
        CachedChangeEvent(
            id=3,
            role_id="role-3",
            role_name="Deprecated Role",
            event_type="deleted",
            scan_timestamp=now - dt.timedelta(days=10),
            azure_updated_on=now - dt.timedelta(days=10),
            summary="Role deprecated",
        ),
    ]


@pytest.fixture
def mock_cache(sample_events: list[CachedChangeEvent]) -> MagicMock:
    """Create a mock cache with sample events."""
    cache = MagicMock()
    cache.get_change_events.return_value = sample_events
    return cache


# =============================================================================
# Unit Tests - Feed Building
# =============================================================================


class TestBuildAtomFeed:
    """Unit tests for Atom feed generation."""

    def test_builds_valid_atom_xml(self, sample_events: list[CachedChangeEvent]):
        """Should build valid Atom 1.0 XML."""
        result = build_atom_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        # Should return bytes
        assert isinstance(result, bytes)

        # Should be valid XML
        root = ET.fromstring(result)
        assert root.tag == "{http://www.w3.org/2005/Atom}feed"

    def test_includes_feed_metadata(self, sample_events: list[CachedChangeEvent]):
        """Should include required feed metadata."""
        result = build_atom_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        title = root.find("atom:title", ns)
        assert title is not None
        assert "Azure RBAC" in (title.text or "")

        # Should have self link
        links = root.findall("atom:link", ns)
        self_link = [lnk for lnk in links if lnk.get("rel") == "self"]
        assert len(self_link) == 1
        assert "changelog.atom" in self_link[0].get("href", "")

    def test_creates_entry_per_event(self, sample_events: list[CachedChangeEvent]):
        """Should create one entry per event."""
        result = build_atom_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        assert len(entries) == len(sample_events)

    def test_entry_contains_role_info(self, sample_events: list[CachedChangeEvent]):
        """Should include role name and event type in entries."""
        result = build_atom_feed(
            sample_events[:1],
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entry = root.find("atom:entry", ns)

        assert entry is not None
        title = entry.find("atom:title", ns)
        assert title is not None
        assert "Test Reader Role" in (title.text or "")
        assert "Created" in (title.text or "")

    def test_handles_empty_events(self):
        """Should handle empty event list."""
        result = build_atom_feed(
            [],
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        assert len(entries) == 0


class TestBuildRssFeed:
    """Unit tests for RSS feed generation."""

    def test_builds_valid_rss_xml(self, sample_events: list[CachedChangeEvent]):
        """Should build valid RSS 2.0 XML."""
        result = build_rss_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        assert isinstance(result, bytes)

        root = ET.fromstring(result)
        assert root.tag == "rss"
        assert root.get("version") == "2.0"

    def test_includes_channel_metadata(self, sample_events: list[CachedChangeEvent]):
        """Should include required channel metadata."""
        result = build_rss_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        channel = root.find("channel")

        assert channel is not None
        assert channel.find("title") is not None
        assert channel.find("link") is not None
        assert channel.find("description") is not None

    def test_creates_item_per_event(self, sample_events: list[CachedChangeEvent]):
        """Should create one item per event."""
        result = build_rss_feed(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []

        assert len(items) == len(sample_events)

    def test_item_contains_role_link(self, sample_events: list[CachedChangeEvent]):
        """Should include link to role detail page."""
        result = build_rss_feed(
            sample_events[:1],
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )

        root = ET.fromstring(result)
        channel = root.find("channel")
        item = channel.find("item") if channel is not None else None

        assert item is not None
        link = item.find("link")
        assert link is not None
        assert "roles/role-1" in (link.text or "")


# =============================================================================
# Unit Tests - Event Filtering
# =============================================================================


class TestGetRecentEvents:
    """Unit tests for event filtering logic."""

    def test_filters_by_cutoff_date(self, mock_cache: MagicMock):
        """Should filter events older than cutoff."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)

        result = get_recent_events(mock_cache, cutoff, limit=100)

        # Should only include events from last 7 days (2 events)
        assert len(result) == 2
        role_ids = [e.role_id for e in result]
        assert "role-1" in role_ids
        assert "role-2" in role_ids
        assert "role-3" not in role_ids

    def test_respects_limit(self, mock_cache: MagicMock):
        """Should respect the limit parameter."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)

        result = get_recent_events(mock_cache, cutoff, limit=2)

        assert len(result) == 2

    def test_sorts_by_date_descending(self, mock_cache: MagicMock):
        """Should sort events newest first."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)

        result = get_recent_events(mock_cache, cutoff, limit=100)

        # Most recent first
        assert result[0].role_id == "role-1"
        assert result[-1].role_id == "role-3"

    def test_handles_empty_cache(self):
        """Should handle empty cache gracefully."""
        cache = MagicMock()
        cache.get_change_events.return_value = []

        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        result = get_recent_events(cache, cutoff, limit=100)

        assert result == []

    def test_handles_none_from_cache(self):
        """Should handle None from cache."""
        cache = MagicMock()
        cache.get_change_events.return_value = None

        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        result = get_recent_events(cache, cutoff, limit=100)

        assert result == []

    def test_handles_naive_datetime_events(self):
        """Should handle events with naive datetimes."""
        # Create event with naive datetime (no timezone)
        naive_time = dt.datetime(2026, 1, 10, 12, 0, 0)  # No tzinfo
        event = CachedChangeEvent(
            id=1,
            role_id="role-naive",
            role_name="Naive Role",
            event_type="created",
            scan_timestamp=naive_time,
            azure_updated_on=None,
            summary="Test",
        )

        cache = MagicMock()
        cache.get_change_events.return_value = [event]

        # Cutoff is timezone-aware
        cutoff = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        result = get_recent_events(cache, cutoff, limit=100)

        # Should not raise, should include the event
        assert len(result) == 1


# =============================================================================
# Integration Tests - HTTP Endpoints
# =============================================================================


class TestFeedEndpointsIntegration:
    """Integration tests for feed HTTP endpoints."""

    @pytest.fixture
    async def test_client_with_events(self, async_session_maker):
        """Create test client with seeded change events."""
        from dataclasses import replace

        from httpx import ASGITransport, AsyncClient

        from azurerbac.cache import get_cache_service
        from azurerbac.cache.models import CachedChangeEvent
        from azurerbac.web import app as app_module
        from azurerbac.web.dependencies import BaseDeps, get_api_deps

        # Get cache and inject test events via proper swap pattern
        cache = get_cache_service()
        now = dt.datetime.now(dt.UTC)
        test_events = [
            CachedChangeEvent(
                id=1,
                role_id="test-role-1",
                role_name="Integration Test Role",
                event_type="created",
                scan_timestamp=now - dt.timedelta(days=1),
                azure_updated_on=now - dt.timedelta(days=1),
                summary="Role created",
            ),
            CachedChangeEvent(
                id=2,
                role_id="test-role-2",
                role_name="Another Test Role",
                event_type="updated",
                scan_timestamp=now - dt.timedelta(days=3),
                azure_updated_on=now - dt.timedelta(days=3),
                summary="Role updated",
            ),
        ]
        new_source = replace(cache._cache.source, all_change_events=test_events)
        cache._cache = replace(cache._cache, source=new_source)

        test_deps = BaseDeps(
            app_cache=cache,
            SessionLocal=async_session_maker,
        )

        app_module.app.dependency_overrides[get_api_deps] = lambda: test_deps

        async with AsyncClient(
            transport=ASGITransport(app=app_module.app),
            base_url="http://test",
        ) as client:
            yield client

        app_module.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_atom_feed_returns_xml(self, test_client_with_events):
        """GET /feeds/changelog.atom returns valid Atom XML."""
        response = await test_client_with_events.get("/feeds/changelog.atom")

        assert response.status_code == 200
        assert "application/atom+xml" in response.headers["content-type"]

        # Should be valid XML
        root = ET.fromstring(response.content)
        assert "feed" in root.tag

    @pytest.mark.asyncio
    async def test_rss_feed_returns_xml(self, test_client_with_events):
        """GET /feeds/changelog.rss returns valid RSS XML."""
        response = await test_client_with_events.get("/feeds/changelog.rss")

        assert response.status_code == 200
        assert "application/rss+xml" in response.headers["content-type"]

        root = ET.fromstring(response.content)
        assert root.tag == "rss"

    @pytest.mark.asyncio
    async def test_feed_respects_days_param(self, test_client_with_events):
        """Feed should filter by days parameter."""
        # Default 30 days should include our 1-day-old event
        response = await test_client_with_events.get("/feeds/changelog.atom")
        root = ET.fromstring(response.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_feed_respects_limit_param(self, test_client_with_events):
        """Feed should respect limit parameter."""
        response = await test_client_with_events.get("/feeds/changelog.atom?limit=1")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_feed_validates_days_range(self, test_client_with_events):
        """Feed should reject invalid days values."""
        # days=0 should be invalid (min is 1)
        response = await test_client_with_events.get("/feeds/changelog.atom?days=0")
        assert response.status_code in (400, 422)

        # days=400 should be invalid (max is 365)
        response = await test_client_with_events.get("/feeds/changelog.atom?days=400")
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_feed_has_cache_header(self, test_client_with_events):
        """Feed should have cache-control header."""
        response = await test_client_with_events.get("/feeds/changelog.atom")

        assert "cache-control" in response.headers
        assert "max-age" in response.headers["cache-control"]


# =============================================================================
# Smoke Tests
# =============================================================================


class TestFeedSmokeTests:
    """Basic smoke tests to verify feeds don't crash."""

    @pytest.fixture
    async def minimal_client(self, async_session_maker):
        """Minimal client without seeded data."""
        from httpx import ASGITransport, AsyncClient

        from azurerbac.cache import get_cache_service
        from azurerbac.web import app as app_module
        from azurerbac.web.dependencies import BaseDeps, get_api_deps

        cache = get_cache_service()

        test_deps = BaseDeps(
            app_cache=cache,
            SessionLocal=async_session_maker,
        )

        app_module.app.dependency_overrides[get_api_deps] = lambda: test_deps

        async with AsyncClient(
            transport=ASGITransport(app=app_module.app),
            base_url="http://test",
        ) as client:
            yield client

        app_module.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_atom_feed_smoke(self, minimal_client):
        """Atom feed should return 200 even with empty cache."""
        response = await minimal_client.get("/feeds/changelog.atom")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_rss_feed_smoke(self, minimal_client):
        """RSS feed should return 200 even with empty cache."""
        response = await minimal_client.get("/feeds/changelog.rss")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_atom_feed_with_params_smoke(self, minimal_client):
        """Atom feed with various params should not crash."""
        params = [
            "?days=7",
            "?days=30&limit=10",
            "?limit=50",
        ]
        for param in params:
            response = await minimal_client.get(f"/feeds/changelog.atom{param}")
            assert response.status_code == 200, f"Failed for {param}"

    @pytest.mark.asyncio
    async def test_rss_feed_with_params_smoke(self, minimal_client):
        """RSS feed with various params should not crash."""
        params = [
            "?days=7",
            "?days=30&limit=10",
            "?limit=50",
        ]
        for param in params:
            response = await minimal_client.get(f"/feeds/changelog.rss{param}")
            assert response.status_code == 200, f"Failed for {param}"


# =============================================================================
# Parametrized Tests - Complete Coverage
# =============================================================================


class TestBuildRichContent:
    """Unit tests for HTML content generation."""

    @pytest.mark.parametrize(
        ("event_type", "expected_contains"),
        [
            (EventType.CREATED, "was <strong>Created</strong>"),
            (EventType.UPDATED, "was <strong>Updated</strong>"),
            (EventType.DELETED, "was <strong>Deleted</strong>"),
        ],
        ids=["created", "updated", "deleted"],
    )
    def test_rich_content_event_types(self, event_type, expected_contains):
        """Should generate appropriate content for each event type."""
        event = CachedChangeEvent(
            id=1,
            role_id="test-role",
            role_name="Test Role",
            event_type=event_type,
            scan_timestamp=dt.datetime.now(dt.UTC),
            azure_updated_on=None,
            summary="Test summary",
        )
        result = _build_rich_content(event, "https://example.com")
        assert expected_contains in result

    def test_rich_content_shows_summary(self):
        """Should show changed fields summary."""
        event = CachedChangeEvent(
            id=1,
            role_id="test-role",
            role_name="Test Role",
            event_type=EventType.UPDATED,
            scan_timestamp=dt.datetime.now(dt.UTC),
            azure_updated_on=None,
            summary="properties.updatedOn, properties.permissions",
        )
        result = _build_rich_content(event, "https://example.com")
        assert "Changed fields:" in result
        assert "properties.updatedOn" in result
        assert "properties.permissions" in result

    def test_rich_content_includes_role_link(self):
        """Should always include link to role details."""
        event = CachedChangeEvent(
            id=1,
            role_id="my-role-id",
            role_name="Test Role",
            event_type=EventType.UPDATED,
            scan_timestamp=dt.datetime.now(dt.UTC),
            azure_updated_on=None,
            summary="Test",
        )
        result = _build_rich_content(event, "https://example.com")
        assert 'href="https://example.com/roles/my-role-id"' in result
        assert "View full role details" in result

    @pytest.mark.parametrize(
        "event_type",
        [EventType.CREATED, EventType.DELETED],
        ids=["created", "deleted"],
    )
    def test_rich_content_no_changed_fields_for_create_delete(self, event_type):
        """Should not show 'Changed fields' for created or deleted events."""
        event = CachedChangeEvent(
            id=1,
            role_id="test-role",
            role_name="Test Role",
            event_type=event_type,
            scan_timestamp=dt.datetime.now(dt.UTC),
            azure_updated_on=None,
            summary="some summary that should be ignored",
        )
        result = _build_rich_content(event, "https://example.com")
        assert "Changed fields:" not in result


class TestFeedBuilderParametrized:
    """Parametrized tests for feed building."""

    @pytest.mark.parametrize(
        ("feed_builder", "expected_root", "content_type_fragment"),
        [
            (build_atom_feed, "{http://www.w3.org/2005/Atom}feed", "atom"),
            (build_rss_feed, "rss", "rss"),
        ],
        ids=["atom", "rss"],
    )
    def test_feed_format_validity(
        self, sample_events, feed_builder, expected_root, content_type_fragment
    ):
        """Should build valid XML for each format."""
        result = feed_builder(
            sample_events,
            site_url="https://example.com",
            updated=dt.datetime.now(dt.UTC),
        )
        root = ET.fromstring(result)
        assert expected_root in root.tag or root.tag == expected_root

    @pytest.mark.parametrize(
        "num_events",
        [0, 1, 5, 50],
        ids=["empty", "single", "few", "many"],
    )
    def test_feed_handles_various_event_counts(self, num_events):
        """Should handle feeds with varying numbers of events."""
        now = dt.datetime.now(dt.UTC)
        events = [
            CachedChangeEvent(
                id=i,
                role_id=f"role-{i}",
                role_name=f"Test Role {i}",
                event_type="updated",
                scan_timestamp=now - dt.timedelta(days=i),
                azure_updated_on=now - dt.timedelta(days=i),
                summary=f"Update {i}",
            )
            for i in range(num_events)
        ]

        atom_result = build_atom_feed(events, "https://example.com", now)
        rss_result = build_rss_feed(events, "https://example.com", now)

        # Both should be valid XML
        atom_root = ET.fromstring(atom_result)
        rss_root = ET.fromstring(rss_result)

        # Atom entries
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        atom_entries = atom_root.findall("atom:entry", ns)
        assert len(atom_entries) == num_events

        # RSS items
        channel = rss_root.find("channel")
        rss_items = channel.findall("item") if channel is not None else []
        assert len(rss_items) == num_events


class TestGetRecentEventsParametrized:
    """Parametrized tests for event filtering."""

    @pytest.mark.parametrize(
        ("cutoff_days", "expected_count"),
        [
            (2, 1),  # Only role-1 (1 day old)
            (7, 2),  # role-1 (1 day) and role-2 (5 days)
            (30, 3),  # All roles
            (365, 3),  # All roles
        ],
        ids=["2-days", "7-days", "30-days", "365-days"],
    )
    def test_filter_by_various_cutoffs(self, mock_cache, cutoff_days, expected_count):
        """Should filter events based on cutoff date."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=cutoff_days)
        result = get_recent_events(mock_cache, cutoff, limit=100)
        assert len(result) == expected_count

    @pytest.mark.parametrize(
        ("limit", "expected_count"),
        [
            (1, 1),
            (2, 2),
            (3, 3),
            (100, 3),  # More than available
        ],
        ids=["limit-1", "limit-2", "limit-3", "limit-more-than-available"],
    )
    def test_limit_parameter(self, mock_cache, limit, expected_count):
        """Should respect limit parameter correctly."""
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
        result = get_recent_events(mock_cache, cutoff, limit=limit)
        assert len(result) == expected_count


class TestFeedEndpointsParametrized:
    """Parametrized integration tests for feed endpoints."""

    @pytest.fixture
    async def test_client_with_events(self, async_session_maker):
        """Create test client with seeded change events."""
        from dataclasses import replace

        from httpx import ASGITransport, AsyncClient

        from azurerbac.cache import get_cache_service
        from azurerbac.cache.models import CachedChangeEvent
        from azurerbac.web import app as app_module
        from azurerbac.web.dependencies import BaseDeps, get_api_deps

        cache = get_cache_service()
        now = dt.datetime.now(dt.UTC)
        test_events = [
            CachedChangeEvent(
                id=1,
                role_id="test-role-1",
                role_name="Test Role",
                event_type="created",
                scan_timestamp=now - dt.timedelta(days=1),
                azure_updated_on=now - dt.timedelta(days=1),
                summary="Role created",
            ),
        ]
        new_source = replace(cache._cache.source, all_change_events=test_events)
        cache._cache = replace(cache._cache, source=new_source)

        test_deps = BaseDeps(
            app_cache=cache,
            SessionLocal=async_session_maker,
        )
        app_module.app.dependency_overrides[get_api_deps] = lambda: test_deps

        async with AsyncClient(
            transport=ASGITransport(app=app_module.app),
            base_url="http://test",
        ) as client:
            yield client

        app_module.app.dependency_overrides.clear()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "expected_content_type", "expected_root"),
        [
            ("/feeds/changelog.atom", "application/atom+xml", "feed"),
            ("/feeds/changelog.rss", "application/rss+xml", "rss"),
        ],
        ids=["atom", "rss"],
    )
    async def test_feed_endpoints_format(
        self, test_client_with_events, endpoint, expected_content_type, expected_root
    ):
        """Each endpoint should return correct content type and XML structure."""
        response = await test_client_with_events.get(endpoint)
        assert response.status_code == 200
        assert expected_content_type in response.headers["content-type"]
        root = ET.fromstring(response.content)
        assert expected_root in root.tag

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "expected_status"),
        [
            ("", 200),
            ("?days=1", 200),
            ("?days=7", 200),
            ("?days=30", 200),
            ("?days=90", 200),
            ("?days=365", 200),
            ("?limit=1", 200),
            ("?limit=50", 200),
            ("?limit=500", 200),
            ("?days=30&limit=10", 200),
            ("?days=0", (400, 422)),  # Invalid: too low
            ("?days=-1", (400, 422)),  # Invalid: negative
            ("?days=400", (400, 422)),  # Invalid: too high
            ("?limit=0", (400, 422)),  # Invalid: too low
            ("?limit=-1", (400, 422)),  # Invalid: negative
            ("?limit=1001", (400, 422)),  # Invalid: too high (>1000)
        ],
        ids=[
            "no-params",
            "days-1",
            "days-7",
            "days-30",
            "days-90",
            "days-365",
            "limit-1",
            "limit-50",
            "limit-500",
            "days-and-limit",
            "invalid-days-0",
            "invalid-days-negative",
            "invalid-days-too-high",
            "invalid-limit-0",
            "invalid-limit-negative",
            "invalid-limit-too-high",
        ],
    )
    async def test_feed_parameter_validation(
        self, test_client_with_events, params, expected_status
    ):
        """Feed endpoints should validate parameters correctly."""
        response = await test_client_with_events.get(f"/feeds/changelog.atom{params}")
        if isinstance(expected_status, tuple):
            assert response.status_code in expected_status
        else:
            assert response.status_code == expected_status
