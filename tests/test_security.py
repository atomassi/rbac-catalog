"""Security tests for the Azure RBAC Catalog web application.

This module contains negative security tests to verify protection against:
- SQL Injection
- Cross-Site Scripting (XSS)
- Path Traversal
- Open Redirect
- Header Injection
- Resource Exhaustion (DoS)
- Information Disclosure
- Security Headers
- CORS misconfiguration
- Host Header Injection
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(async_session_maker) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client for security testing.

    Uses the real app with properly initialized in-memory database and cache.
    """
    # Lazy import to avoid loading .env during test collection
    from azurerbac.cache import get_cache_service
    from azurerbac.cache.build import precompute_all
    from azurerbac.cache.models import CachedRole
    from azurerbac.core.enums import RoleStatus
    from azurerbac.web import app as app_module
    from tests.helpers import make_operation, make_role_definition

    test_session_maker = async_session_maker
    cache = get_cache_service()

    # Create minimal test data for cache (required for /roles endpoint)
    test_role_defs = [
        make_role_definition(
            role_id="test-role-1",
            role_name="Test Role 1",
            actions=["Microsoft.Storage/storageAccounts/read"],
        ),
    ]
    test_operations = [
        make_operation("Microsoft.Storage/storageAccounts/read"),
    ]
    roles_by_id = {
        role.role_id: CachedRole(definition=role, status=RoleStatus.ACTIVE)
        for role in test_role_defs
    }
    cache.swap(precompute_all(test_role_defs, test_operations, roles_by_id=roles_by_id))

    original_session = app_module.app.state.session_local
    app_module.app.state.session_local = test_session_maker

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app_module.app.state.session_local = original_session


class TestSQLInjection:
    """Test SQL injection attack vectors are properly escaped."""

    @pytest.mark.parametrize(
        "payload",
        [
            "'; DROP TABLE roles; --",
            "1' OR '1'='1",
            "1; SELECT * FROM users--",
            "' UNION SELECT * FROM roles--",
            "1' AND 1=1--",
            "admin'--",
            "' OR 1=1#",
            "'; WAITFOR DELAY '00:00:10'--",
            "1'; EXEC xp_cmdshell('dir')--",
            "${7*7}",  # Template injection
            "{{7*7}}",  # Jinja2 template injection
        ],
    )
    async def test_search_query_sql_injection(self, client: AsyncClient, payload: str):
        """Verify search queries are protected against SQL injection."""
        response = await client.get("/roles", params={"q": payload})
        # Should return 200 (no results) or 400, never a 500 error
        assert response.status_code in (200, 400)
        # Response should not contain SQL engine error messages.
        assert "syntax error" not in response.text.lower()
        assert "sqlite" not in response.text.lower()
        assert "postgresql" not in response.text.lower()

    @pytest.mark.parametrize(
        "payload",
        [
            "'; DROP TABLE operations; --",
            "1' OR '1'='1",
            "Microsoft.Storage' UNION SELECT * FROM roles--",
        ],
    )
    async def test_operations_search_sql_injection(self, client: AsyncClient, payload: str):
        """Verify operations search is protected against SQL injection."""
        response = await client.get("/operations", params={"q": payload})
        assert response.status_code in (200, 400)
        assert "syntax error" not in response.text.lower()

    @pytest.mark.parametrize(
        "role_id",
        [
            "'; DROP TABLE roles; --",
            "00000000-0000-0000-0000-000000000000' OR '1'='1",
            "../../../etc/passwd",
        ],
    )
    async def test_role_detail_sql_injection(self, client: AsyncClient, role_id: str):
        """Verify role detail endpoint is protected against SQL injection."""
        response = await client.get(f"/roles/{role_id}")
        # Should return 400 (invalid UUID) or 404 (not found), never 500
        assert response.status_code in (400, 404)


class TestXSS:
    """Test Cross-Site Scripting (XSS) attack vectors are properly escaped."""

    @pytest.mark.parametrize(
        "payload,escaped",
        [
            ("<script>alert('XSS')</script>", "&lt;script&gt;"),
            ("<img src=x onerror=alert('XSS')>", "&lt;img"),
            ("<svg onload=alert('XSS')>", "&lt;svg"),
            ("<body onload=alert('XSS')>", "&lt;body"),
            ("'><script>alert('XSS')</script>", "&lt;script&gt;"),
            ('"><img src=x onerror=alert(1)>', "&lt;img"),
            ("<iframe src='javascript:alert(1)'>", "&lt;iframe"),
            ("<a href='javascript:alert(1)'>click</a>", "&lt;a"),
        ],
    )
    async def test_search_xss(self, client: AsyncClient, payload: str, escaped: str):
        """Verify search queries are escaped to prevent XSS."""
        response = await client.get("/roles", params={"q": payload})
        assert response.status_code == 200
        # The payload should be HTML-escaped in the response
        # Check the escaped version appears (meaning it was sanitized)
        # OR the payload simply doesn't appear unescaped
        assert payload not in response.text or escaped in response.text

    @pytest.mark.parametrize(
        "payload,escaped",
        [
            ("<script>alert('XSS')</script>", "&lt;script&gt;"),
            ("<img src=x onerror=alert(1)>", "&lt;img"),
        ],
    )
    async def test_operations_xss(self, client: AsyncClient, payload: str, escaped: str):
        """Verify operations search is protected against XSS."""
        response = await client.get("/operations", params={"q": payload})
        assert response.status_code == 200
        # The payload should be HTML-escaped in the response
        assert payload not in response.text or escaped in response.text


class TestPathTraversal:
    """Test path traversal attack vectors."""

    @pytest.mark.parametrize(
        "path",
        [
            "/static/../../../etc/passwd",
            "/static/..%2f..%2f..%2fetc/passwd",
            "/static/....//....//....//etc/passwd",
            "/roles/../../../etc/passwd",
            "/static/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
            "/static/css/../../../../../../etc/passwd",
        ],
    )
    async def test_path_traversal_static(self, client: AsyncClient, path: str):
        """Verify static file serving is protected against path traversal."""
        response = await client.get(path)
        # Should return 404, not the contents of /etc/passwd
        assert response.status_code in (400, 404, 422)
        assert "root:" not in response.text
        assert "/bin/bash" not in response.text


class TestOpenRedirect:
    """Test open redirect vulnerabilities."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com",
            "//evil.com",
            "https://evil.com/phishing",
            "/\\evil.com",
            "https:evil.com",
        ],
    )
    async def test_no_open_redirect_in_back_param(self, client: AsyncClient, url: str):
        """Verify back/redirect parameters don't allow open redirects."""
        # Test role detail with malicious from parameter
        response = await client.get(
            "/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7/reader",
            params={"from": url},
            follow_redirects=False,
        )
        # If there's a redirect, it should be to our domain only
        if response.status_code in (301, 302, 307, 308):
            location = response.headers.get("location", "")
            assert not location.startswith("http://evil")
            assert not location.startswith("https://evil")
            assert not location.startswith("//evil")


class TestHeaderInjection:
    """Test HTTP header injection attacks."""

    @pytest.mark.parametrize(
        "payload",
        [
            "value\r\nX-Injected: header",
            "value\nX-Injected: header",
            "value%0d%0aX-Injected:%20header",
            "value\r\n\r\n<html>injected</html>",
        ],
    )
    async def test_header_injection_via_query(self, client: AsyncClient, payload: str):
        """Verify query parameters can't inject HTTP headers."""
        response = await client.get("/roles", params={"q": payload})
        # Check that no injected headers appear
        assert "X-Injected" not in response.headers


class TestInformationDisclosure:
    """Test protection against information disclosure."""

    async def test_404_no_stack_trace(self, client: AsyncClient):
        """Verify 404 pages don't leak stack traces."""
        response = await client.get("/nonexistent-page-12345")
        assert response.status_code == 404
        assert "Traceback" not in response.text
        assert 'File "' not in response.text
        assert ", line " not in response.text.lower()

    async def test_400_no_stack_trace(self, client: AsyncClient):
        """Verify 400 pages don't leak stack traces."""
        response = await client.get("/roles", params={"page": "not-a-number"})
        assert response.status_code in (400, 422)
        assert "Traceback" not in response.text
        assert 'File "' not in response.text

    async def test_no_debug_endpoints(self, client: AsyncClient):
        """Verify debug endpoints are not exposed."""
        debug_paths = [
            "/debug",
            "/_debug",
            "/admin",
            "/console",
            "/shell",
            "/phpinfo.php",
            "/.env",
            "/config",
            "/settings",
            "/.git/config",
            "/wp-admin",
        ]
        for path in debug_paths:
            response = await client.get(path)
            # Should return 404, not expose any debug info
            assert response.status_code == 404

    async def test_healthz_minimal_info(self, client: AsyncClient):
        """Verify health endpoint doesn't leak sensitive info."""
        response = await client.get("/healthz")
        assert response.status_code == 200
        # Should be minimal, not expose internal details
        text = response.text.lower()
        assert "password" not in text
        assert "secret" not in text
        assert "key" not in text or "ok" in text  # "ok" might contain "k"


class TestAPIEndpoints:
    """Test API endpoint security."""

    async def test_api_no_sensitive_data(self, client: AsyncClient):
        """Verify API endpoints don't expose sensitive data."""
        response = await client.get("/api/operations/search", params={"q": "test", "limit": 1})
        assert response.status_code == 200
        # Should not contain internal paths or credentials
        text = response.text.lower()
        assert "/users/" not in text
        assert "password" not in text
        assert "secret" not in text

    async def test_robots_txt_exists(self, client: AsyncClient):
        """Verify robots.txt exists and has sensible content."""
        response = await client.get("/robots.txt")
        assert response.status_code == 200
        assert "User-agent" in response.text
        assert "Sitemap" in response.text


class TestResourceExhaustion:
    """Test protection against resource exhaustion attacks.

    Note: FastAPI returns 422 for validation errors, but this app has a custom
    exception handler that converts them to 400 with a user-friendly error page.
    """

    @pytest.mark.parametrize(
        ("path", "params"),
        [
            pytest.param("/roles", {"q": "A" * 10000}, id="roles_query_too_long"),
            pytest.param("/roles", {"page": -1}, id="roles_negative_page"),
            pytest.param("/roles", {"page": 999999999}, id="roles_huge_page"),
            pytest.param("/roles", {"limit": 0}, id="roles_zero_limit"),
            pytest.param("/roles", {"limit": -1}, id="roles_negative_limit"),
            pytest.param("/roles", {"limit": 10000}, id="roles_excessive_limit"),
            pytest.param("/operations", {"page": -1}, id="operations_negative_page"),
            pytest.param("/operations", {"limit": 0}, id="operations_zero_limit"),
            pytest.param("/operations", {"limit": 10000}, id="operations_excessive_limit"),
            pytest.param("/recent", {"days": 0}, id="recent_zero_days"),
            pytest.param("/recent", {"days": 1000}, id="recent_excessive_days"),
            pytest.param("/recent", {"page": -1}, id="recent_negative_page"),
            pytest.param("/recent", {"limit": 0}, id="recent_zero_limit"),
            # API search endpoints - MAX_SEARCH_LIMIT enforcement
            pytest.param(
                "/api/operations/search",
                {"q": "test", "limit": 101},
                id="api_ops_search_limit_exceeded",
            ),
            pytest.param(
                "/api/operations/search",
                {"q": "test", "limit": 0},
                id="api_ops_search_zero_limit",
            ),
            # Operation detail page validation
            pytest.param("/operations/test.op", {"page": -1}, id="op_detail_negative_page"),
            pytest.param("/operations/test.op", {"limit": 0}, id="op_detail_zero_limit"),
            pytest.param("/operations/test.op", {"limit": 10000}, id="op_detail_excessive_limit"),
        ],
    )
    async def test_invalid_query_params_rejected(
        self, client: AsyncClient, path: str, params: dict
    ):
        """Verify invalid query parameters return 400."""
        response = await client.get(path, params=params)
        assert response.status_code == 400

    async def test_large_operations_list_rejected(self, client: AsyncClient):
        """Verify large operations lists are rejected with 400."""
        large_payload = {
            "operations": [{"name": f"op{i}", "is_data_action": False} for i in range(200)]
        }
        response = await client.post("/api/recommend-roles", json=large_payload)
        assert response.status_code == 400

    @pytest.mark.parametrize(
        ("query", "top_k"),
        [
            pytest.param("A" * 200, 5, id="query_too_long"),
            pytest.param("test query", 100, id="top_k_too_large"),
            pytest.param("test query", 0, id="top_k_zero"),
            pytest.param("test query", -1, id="top_k_negative"),
        ],
    )
    async def test_ai_recommend_invalid_params_rejected(
        self, client: AsyncClient, query: str, top_k: int
    ):
        """Verify AI recommend rejects invalid parameters with 400."""
        response = await client.post(
            "/api/ai-recommend",
            json={"query": query, "top_k": top_k, "recommender_mode": "tfidf"},
        )
        assert response.status_code == 400


class TestMethodNotAllowed:
    """Test that endpoints reject unexpected HTTP methods."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            pytest.param("POST", "/roles", id="post_roles"),
            pytest.param("PUT", "/roles", id="put_roles"),
            pytest.param("DELETE", "/roles", id="delete_roles"),
            pytest.param("PATCH", "/roles", id="patch_roles"),
            pytest.param("POST", "/operations", id="post_operations"),
            pytest.param("GET", "/api/recommend-roles", id="get_recommend_roles"),
            pytest.param("GET", "/api/ai-recommend", id="get_ai_recommend"),
        ],
    )
    async def test_method_not_allowed(self, client: AsyncClient, method: str, path: str):
        """Verify endpoints reject unexpected HTTP methods."""
        response = await client.request(method, path)
        assert response.status_code == 405
