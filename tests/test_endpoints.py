"""Tests for FastAPI endpoints and error handling."""

import pytest
from httpx import ASGITransport, AsyncClient

from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus
from azurerbac.core.constants import EventType, RoleStatus


def _make_test_snapshot(
    session,
    role_id: str,
    role_name: str,
    role_type: str = "BuiltInRole",
    status: str | RoleStatus = RoleStatus.ACTIVE,
    description: str = "A test role",
) -> Role:
    """Helper to create Role + RoleHistory for tests.

    History auto-links via history relationship (current_version = history[0]).
    """
    role_json_data = {
        "id": f"/providers/Microsoft.Authorization/roleDefinitions/{role_id}",
        "name": role_id,
        "properties": {
            "roleName": role_name,
            "type": role_type,
            "description": description,
        },
    }
    snap = Role(role_id=role_id, role_name=role_name, status=status)
    history = RoleHistory(
        role_id=role_id,
        version_number=1,
        role_name=role_name,
        event_type=EventType.CREATED,
        role_json=role_json_data,
        diff_json={},
        summary="Role created",
    )
    session.add(snap)
    session.add(history)
    return snap


@pytest.fixture
async def test_client(async_session_maker):
    """Create a test client with in-memory database."""
    # Lazy import to avoid loading .env during test collection
    from azurerbac.cache import get_cache_service
    from azurerbac.web import app as app_module
    from azurerbac.web.dependencies import (
        BaseDeps,
        DashboardDeps,
        PagesDeps,
        get_api_deps,
        get_dashboard_deps,
        get_pages_deps,
    )

    test_session_maker = async_session_maker
    cache = get_cache_service().container

    # Store original session maker
    original_session = app_module.SessionLocal

    # Get original deps from app.state
    original_api_deps = getattr(app_module.app.state, "api_deps", None)
    original_dashboard_deps = getattr(app_module.app.state, "dashboard_deps", None)
    original_pages_deps = getattr(app_module.app.state, "pages_deps", None)

    # Create test BaseDeps with test session
    test_api_deps = BaseDeps(
        app_cache=cache,
        SessionLocal=test_session_maker,
    )

    # Create test DashboardDeps with test session
    test_dashboard_deps = DashboardDeps(
        app_cache=cache,
        SessionLocal=test_session_maker,
        Role=Role,
        RoleHistory=RoleHistory,
        RoleScanStatus=RoleScanStatus,
        Operation=Operation,
        templates=app_module.templates,
    )

    # Create test PagesDeps with test session
    test_pages_deps = PagesDeps(
        app_cache=cache,
        SessionLocal=test_session_maker,
        Role=Role,
        RoleHistory=RoleHistory,
        Operation=Operation,
        templates=app_module.templates,
    )

    # Update deps via app.state and dependency overrides
    app_module.app.state.api_deps = test_api_deps
    app_module.app.state.dashboard_deps = test_dashboard_deps
    app_module.app.state.pages_deps = test_pages_deps
    app_module.app.dependency_overrides[get_api_deps] = lambda: test_api_deps
    app_module.app.dependency_overrides[get_dashboard_deps] = lambda: test_dashboard_deps
    app_module.app.dependency_overrides[get_pages_deps] = lambda: test_pages_deps

    # Update app-level session for any remaining uses
    app_module.SessionLocal = test_session_maker

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, test_session_maker

    # Restore original session makers and deps
    app_module.SessionLocal = original_session
    if original_api_deps:
        app_module.app.state.api_deps = original_api_deps
    if original_dashboard_deps:
        app_module.app.state.dashboard_deps = original_dashboard_deps
    if original_pages_deps:
        app_module.app.state.pages_deps = original_pages_deps
    app_module.app.dependency_overrides.clear()


# =============================================================================
# Role Detail Tests
# =============================================================================


class TestRoleDetail:
    """Tests for role detail endpoint."""

    # Valid UUID for testing
    TEST_ROLE_ID = "00000000-0000-0000-0000-000000000001"

    @pytest.mark.asyncio
    async def test_existing_role_returns_200(self, test_client):
        """Test that accessing existing role returns 200."""
        client, session_maker = test_client
        async with session_maker() as session:
            _make_test_snapshot(session, self.TEST_ROLE_ID, "Test Role")
            await session.commit()

        response = await client.get(f"/roles/{self.TEST_ROLE_ID}/test-role")
        assert response.status_code == 200
        # Basic smoke-check that we rendered the role details, not an error page.
        assert "Test Role" in response.text
        assert self.TEST_ROLE_ID in response.text

    @pytest.mark.asyncio
    async def test_role_redirects_to_slug(self, test_client):
        """Test that accessing role without slug redirects to slug URL."""
        client, session_maker = test_client
        async with session_maker() as session:
            existing = await session.get(Role, self.TEST_ROLE_ID)
            if not existing:
                _make_test_snapshot(session, self.TEST_ROLE_ID, "Test Role")
                await session.commit()

        response = await client.get(f"/roles/{self.TEST_ROLE_ID}", follow_redirects=False)
        assert response.status_code == 301
        assert f"/roles/{self.TEST_ROLE_ID}/test-role" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_role_id_without_dashes_returns_200(self, test_client):
        """Test that role_id without dashes (32-char hex) is normalized and works."""
        client, session_maker = test_client
        # UUID with dashes
        role_id_dashes = "754c1a27-40dc-4708-8ad4-2bffdeee09e8"
        # Same UUID without dashes
        role_id_no_dashes = "754c1a2740dc47088ad42bffdeee09e8"

        async with session_maker() as session:
            existing = await session.get(Role, role_id_dashes)
            if not existing:
                _make_test_snapshot(session, role_id_dashes, "Test UUID Role")
                await session.commit()

        # Access with dashes - should work directly
        response = await client.get(f"/roles/{role_id_dashes}/test-uuid-role")
        assert response.status_code == 200
        assert "Test UUID Role" in response.text

        # Access without dashes - should normalize and work
        response = await client.get(f"/roles/{role_id_no_dashes}/test-uuid-role")
        assert response.status_code == 200
        assert "Test UUID Role" in response.text

    @pytest.mark.asyncio
    async def test_role_id_without_dashes_redirects_to_slug(self, test_client):
        """Test that role_id without dashes redirects to proper slug URL."""
        client, session_maker = test_client
        role_id_dashes = "754c1a27-40dc-4708-8ad4-2bffdeee09e8"
        role_id_no_dashes = "754c1a2740dc47088ad42bffdeee09e8"

        async with session_maker() as session:
            existing = await session.get(Role, role_id_dashes)
            if not existing:
                _make_test_snapshot(session, role_id_dashes, "Test UUID Role")
                await session.commit()

        # Access without dashes and no slug - should redirect
        response = await client.get(f"/roles/{role_id_no_dashes}", follow_redirects=False)
        assert response.status_code == 301
        # Redirect should use the canonical UUID with dashes
        assert f"/roles/{role_id_dashes}/test-uuid-role" in response.headers["location"]

    @pytest.mark.parametrize(
        ("role_id", "expected_status"),
        [
            # Valid formats - should work (role doesn't exist, so 404)
            pytest.param("754c1a27-40dc-4708-8ad4-2bffdeee09e9", 404, id="valid_with_dashes"),
            pytest.param("754c1a2740dc47088ad42bffdeee09e9", 404, id="valid_without_dashes"),
            pytest.param("754C1A27-40DC-4708-8AD4-2BFFDEEE09E9", 404, id="valid_uppercase"),
            pytest.param("754c1a27-40dc-4708-8ad4-2BFFDEEE09E9", 404, id="valid_mixed_case"),
            # Invalid formats - should return 400
            pytest.param("not-a-valid-uuid", 400, id="invalid_not_hex"),
            pytest.param("12345", 400, id="invalid_too_short"),
            pytest.param("zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz", 400, id="invalid_non_hex_chars"),
            pytest.param("754c1a27-40dc-4708-8ad4", 400, id="invalid_incomplete"),
            pytest.param("754c1a27-40dc-4708-8ad4-2bffdeee09e8-extra", 400, id="invalid_too_long"),
        ],
    )
    @pytest.mark.asyncio
    async def test_role_id_format_validation(self, test_client, role_id: str, expected_status: int):
        """Test UUID format validation for role_id path parameter."""
        client, _ = test_client
        response = await client.get(f"/roles/{role_id}")
        assert response.status_code == expected_status


# =============================================================================
# API Endpoint Tests
# =============================================================================


class TestAPIEndpoints:
    """Tests for API endpoints to ensure dependency injection works correctly."""

    @pytest.mark.asyncio
    async def test_api_operations_search_returns_json(self, test_client):
        """Test that /api/operations/search returns JSON, not 400 error."""
        client, _ = test_client
        response = await client.get("/api/operations/search?q=test&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "operations" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_api_operations_search_with_wildcard(self, test_client):
        """Test that wildcard queries work correctly."""
        client, _ = test_client
        response = await client.get("/api/operations/search?q=*/read&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "operations" in data
        assert "is_wildcard_search" in data

    @pytest.mark.asyncio
    async def test_api_operations_search_short_query(self, test_client):
        """Test that short queries return empty results with message."""
        client, _ = test_client
        response = await client.get("/api/operations/search?q=a&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert "message" in data

    @pytest.mark.asyncio
    async def test_api_count_matches_returns_json(self, test_client):
        """Test that /api/operations/count-matches returns JSON, not 400 error."""
        client, _ = test_client
        response = await client.get(
            "/api/operations/count-matches?pattern=*/read&is_data_action=false"
        )
        assert response.status_code == 200
        data = response.json()
        assert "pattern" in data
        assert "count" in data
        assert "is_data_action" in data

    @pytest.mark.asyncio
    async def test_api_recommend_roles_returns_json(self, test_client):
        """Test that /api/recommend-roles POST returns JSON."""
        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            json={
                "operations": [
                    {"name": "Microsoft.Compute/virtualMachines/read", "is_data_action": False}
                ]
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "roles" in data
        assert "requested_operations" in data


# =============================================================================
# Dashboard Sorting Tests
# =============================================================================


class TestDashboardSorting:
    """Tests for /roles endpoint sorting functionality.

    All sort fields must be tested to prevent regressions where
    a sort field causes 500 errors (e.g., sorting by a property
    that doesn't map to a database column).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("sort_field", "order"),
        [
            pytest.param("name", "asc", id="name_asc"),
            pytest.param("name", "desc", id="name_desc"),
            pytest.param("id", "asc", id="id_asc"),
            pytest.param("id", "desc", id="id_desc"),
            pytest.param("updated", "asc", id="updated_asc"),
            pytest.param("updated", "desc", id="updated_desc"),
            pytest.param("actions", "asc", id="actions_asc"),
            pytest.param("actions", "desc", id="actions_desc"),
            pytest.param("data_actions", "asc", id="data_actions_asc"),
            pytest.param("data_actions", "desc", id="data_actions_desc"),
        ],
    )
    async def test_roles_sort_returns_200(self, test_client, sort_field, order):
        """Test sorting roles by various fields and orders returns 200."""
        client, _ = test_client
        response = await client.get(f"/roles?sort={sort_field}&order={order}")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_default_sort(self, test_client):
        """Test that /roles without sort params returns 200 (default sort)."""
        client, _ = test_client
        response = await client.get("/roles")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_invalid_sort_uses_default(self, test_client):
        """Test that invalid sort field falls back to default (name)."""
        client, _ = test_client
        response = await client.get("/roles?sort=invalid_field&order=asc")
        assert response.status_code == 200
