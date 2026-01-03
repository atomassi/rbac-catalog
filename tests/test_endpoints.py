"""Tests for FastAPI endpoints and error handling."""

import pytest
from httpx import ASGITransport, AsyncClient

from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus


def _make_test_snapshot(
    session,
    role_id: str,
    role_name: str,
    role_type: str = "BuiltInRole",
    status: str = "active",
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
        event_type="created",
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
    from azurerbac.web import app as app_module
    from azurerbac.web.dependencies import (
        APIDeps,
        DashboardDeps,
        PagesDeps,
        get_api_deps,
        get_dashboard_deps,
        get_pages_deps,
    )

    test_session_maker = async_session_maker

    # Store original session maker
    original_session = app_module.SessionLocal

    # Get original deps from app.state
    original_api_deps = getattr(app_module.app.state, "api_deps", None)
    original_dashboard_deps = getattr(app_module.app.state, "dashboard_deps", None)
    original_pages_deps = getattr(app_module.app.state, "pages_deps", None)

    # Wrap cache functions to use test session maker
    async def test_get_all_operations():
        return await app_module.get_all_operations(
            cache=app_module.app_cache, session_factory=test_session_maker
        )

    async def test_get_all_role_jsons():
        return await app_module.get_all_role_jsons(
            cache=app_module.app_cache, session_factory=test_session_maker
        )

    async def test_get_operations_for_recommender():
        return await app_module.get_operations_for_recommender(
            cache=app_module.app_cache, session_factory=test_session_maker
        )

    # Create test APIDeps with test session
    test_api_deps = APIDeps(
        app_cache=app_module.app_cache,
        SessionLocal=test_session_maker,
        Role=Role,
        get_all_operations=test_get_all_operations,
        get_all_role_jsons=test_get_all_role_jsons,
        get_operations_for_recommender=test_get_operations_for_recommender,
    )

    # Create test DashboardDeps with test session
    test_dashboard_deps = DashboardDeps(
        app_cache=app_module.app_cache,
        SessionLocal=test_session_maker,
        Role=Role,
        RoleHistory=RoleHistory,
        RoleScanStatus=RoleScanStatus,
        Operation=Operation,
        templates=app_module.templates,
    )

    # Create test PagesDeps with test session
    test_pages_deps = PagesDeps(
        app_cache=app_module.app_cache,
        SessionLocal=test_session_maker,
        Role=Role,
        RoleHistory=RoleHistory,
        Operation=Operation,
        templates=app_module.templates,
        get_all_operations=test_get_all_operations,
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

    @pytest.mark.asyncio
    async def test_invalid_role_id_returns_404(self, test_client):
        """Test that invalid UUID format returns 404."""
        client, _ = test_client

        # Invalid UUID - not hex
        response = await client.get("/roles/not-a-valid-uuid")
        assert response.status_code == 404

        # Invalid UUID - wrong length
        response = await client.get("/roles/12345")
        assert response.status_code == 404

        # Invalid UUID - contains non-hex characters
        response = await client.get("/roles/zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz")
        assert response.status_code == 404


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
    async def test_roles_sort_by_name_asc(self, test_client):
        """Test sorting roles by name ascending (default)."""
        client, _ = test_client
        response = await client.get("/roles?sort=name&order=asc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_name_desc(self, test_client):
        """Test sorting roles by name descending."""
        client, _ = test_client
        response = await client.get("/roles?sort=name&order=desc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_id_asc(self, test_client):
        """Test sorting roles by ID ascending."""
        client, _ = test_client
        response = await client.get("/roles?sort=id&order=asc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_id_desc(self, test_client):
        """Test sorting roles by ID descending."""
        client, _ = test_client
        response = await client.get("/roles?sort=id&order=desc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_updated_asc(self, test_client):
        """Test sorting roles by updated date ascending.

        This was broken because Role.updated_on is a property, not a DB column.
        The fix: needs_python_sort must include 'updated'.
        """
        client, _ = test_client
        response = await client.get("/roles?sort=updated&order=asc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_updated_desc(self, test_client):
        """Test sorting roles by updated date descending."""
        client, _ = test_client
        response = await client.get("/roles?sort=updated&order=desc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_actions_asc(self, test_client):
        """Test sorting roles by actions count ascending."""
        client, _ = test_client
        response = await client.get("/roles?sort=actions&order=asc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_actions_desc(self, test_client):
        """Test sorting roles by actions count descending."""
        client, _ = test_client
        response = await client.get("/roles?sort=actions&order=desc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_data_actions_asc(self, test_client):
        """Test sorting roles by data actions count ascending."""
        client, _ = test_client
        response = await client.get("/roles?sort=data_actions&order=asc")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_roles_sort_by_data_actions_desc(self, test_client):
        """Test sorting roles by data actions count descending."""
        client, _ = test_client
        response = await client.get("/roles?sort=data_actions&order=desc")
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
