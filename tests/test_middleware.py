"""Tests for HTTP middleware functions."""

import pytest
from httpx import ASGITransport, AsyncClient

from azurerbac.web.middleware import (
    NEW_DOMAIN,
)


@pytest.fixture
async def test_client(async_session_maker):
    """Create a test client with in-memory database."""
    # Lazy import to avoid loading .env during test collection
    from azurerbac.web import app as app_module

    test_session_maker = async_session_maker
    original_session = app_module.app.state.session_local
    app_module.app.state.session_local = test_session_maker

    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, test_session_maker

    app_module.app.state.session_local = original_session


# =============================================================================
# Cache Headers Tests
# =============================================================================


class TestCacheHeaders:
    """Tests for add_cache_headers middleware."""

    @pytest.mark.asyncio
    async def test_api_endpoints_no_cache(self, test_client):
        """Test that API endpoints return no-store cache control."""
        client, _ = test_client
        response = await client.get("/api/search/operations")
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_healthz_no_cache(self, test_client):
        """Test that healthz endpoint returns no-store cache control."""
        client, _ = test_client
        response = await client.get("/healthz")
        assert response.headers.get("cache-control") == "no-store"

    @pytest.mark.asyncio
    async def test_static_assets_long_cache(self, test_client):
        """Test that static assets get long cache headers."""
        client, _ = test_client
        response = await client.get("/static/css/styles.css")
        cache_control = response.headers.get("cache-control", "")
        assert "max-age=31536000" in cache_control
        assert "immutable" in cache_control
        assert response.headers.get("vary") == "Accept-Encoding"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/", "/recent", "/roles", "/recommend"])
    async def test_main_pages_moderate_cache(self, test_client, path):
        """Test that main pages get moderate cache headers."""
        client, _ = test_client
        response = await client.get(path, follow_redirects=True)
        cache_control = response.headers.get("cache-control", "")
        # Main pages should have s-maxage for CDN caching
        assert "s-maxage" in cache_control
        assert "max-age" in cache_control

    @pytest.mark.asyncio
    async def test_role_detail_cache_headers(self, test_client):
        """Test that role detail pages get appropriate cache headers."""
        client, _ = test_client
        # This will 404, but middleware still adds headers before route handler
        response = await client.get("/roles/test-role")
        cache_control = response.headers.get("cache-control", "")
        # Role detail pages should have longer s-maxage (30 minutes)
        assert "s-maxage=1800" in cache_control


# =============================================================================
# Security Headers Tests
# =============================================================================


class TestSecurityHeaders:
    """Tests for add_security_headers middleware."""

    @pytest.mark.asyncio
    async def test_x_frame_options_header(self, test_client):
        """Test that X-Frame-Options is set to DENY."""
        client, _ = test_client
        response = await client.get("/recent")
        assert response.headers.get("x-frame-options") == "DENY"

    @pytest.mark.asyncio
    async def test_referrer_policy_header(self, test_client):
        """Test that Referrer-Policy is set correctly."""
        client, _ = test_client
        response = await client.get("/recent")
        assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    @pytest.mark.asyncio
    async def test_xss_protection_header(self, test_client):
        """Test that X-XSS-Protection is set."""
        client, _ = test_client
        response = await client.get("/recent")
        assert response.headers.get("x-xss-protection") == "1; mode=block"

    @pytest.mark.asyncio
    async def test_content_security_policy_header(self, test_client):
        """Test that Content-Security-Policy is set with expected directives."""
        client, _ = test_client
        response = await client.get("/recent")
        csp = response.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "script-src" in csp
        assert "frame-ancestors 'none'" in csp

    @pytest.mark.asyncio
    async def test_permissions_policy_header(self, test_client):
        """Test that Permissions-Policy disables unnecessary features."""
        client, _ = test_client
        response = await client.get("/recent")
        permissions = response.headers.get("permissions-policy", "")
        assert "camera=()" in permissions
        assert "microphone=()" in permissions

    @pytest.mark.asyncio
    async def test_api_endpoints_have_noindex_header(self, test_client):
        """Test that API endpoints have X-Robots-Tag noindex."""
        client, _ = test_client
        response = await client.get("/api/search/operations")
        robots_tag = response.headers.get("x-robots-tag", "")
        assert "noindex" in robots_tag

    @pytest.mark.asyncio
    async def test_healthz_has_noindex_header(self, test_client):
        """Test that healthz endpoint has X-Robots-Tag noindex."""
        client, _ = test_client
        response = await client.get("/healthz")
        robots_tag = response.headers.get("x-robots-tag", "")
        assert "noindex" in robots_tag

    @pytest.mark.asyncio
    async def test_regular_pages_no_noindex_header(self, test_client):
        """Test that regular pages do NOT have X-Robots-Tag noindex."""
        client, _ = test_client
        response = await client.get("/recent")
        robots_tag = response.headers.get("x-robots-tag", "")
        # Regular pages should be indexable
        assert "noindex" not in robots_tag


# =============================================================================
# Domain Redirect Tests
# =============================================================================


class TestDomainRedirect:
    """Tests for redirect_old_domain middleware."""

    @pytest.mark.asyncio
    async def test_old_appservice_domain_redirects(self, test_client):
        """Test redirect from old App Service domain."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers={"x-forwarded-host": "azurerbac-builtinroles.azurewebsites.net"},
            follow_redirects=False,
        )
        assert response.status_code == 301
        assert NEW_DOMAIN in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_old_frontdoor_domain_redirects(self, test_client):
        """Test redirect from old Front Door domain."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers={"x-forwarded-host": "azurerbac-builtinroles-atgjgtc9a9exbahe.z02.azurefd.net"},
            follow_redirects=False,
        )
        assert response.status_code == 301
        assert NEW_DOMAIN in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_redirect_preserves_path(self, test_client):
        """Test that redirect preserves the request path."""
        client, _ = test_client
        response = await client.get(
            "/roles/test-role",
            headers={"x-forwarded-host": "azurerbac-builtinroles.azurewebsites.net"},
            follow_redirects=False,
        )
        assert response.status_code == 301
        location = response.headers.get("location", "")
        assert "/roles/test-role" in location

    @pytest.mark.asyncio
    async def test_redirect_preserves_query_string(self, test_client):
        """Test that redirect preserves query parameters."""
        client, _ = test_client
        response = await client.get(
            "/roles?page=2&limit=25",
            headers={"x-forwarded-host": "azurerbac-builtinroles.azurewebsites.net"},
            follow_redirects=False,
        )
        assert response.status_code == 301
        location = response.headers.get("location", "")
        assert "page=2" in location
        assert "limit=25" in location

    @pytest.mark.asyncio
    async def test_canonical_domain_no_redirect(self, test_client):
        """Test that requests to canonical domain are not redirected."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers={"x-forwarded-host": "rbac-catalog.dev"},
            follow_redirects=False,
        )
        # Should NOT be a redirect
        assert response.status_code != 301

    @pytest.mark.asyncio
    async def test_localhost_no_redirect(self, test_client):
        """Test that localhost requests are not redirected."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers={"host": "localhost:8000"},
            follow_redirects=False,
        )
        assert response.status_code != 301


# =============================================================================
# Integration Tests
# =============================================================================


class TestMiddlewareIntegration:
    """Integration tests for all middleware working together."""

    @pytest.mark.asyncio
    async def test_all_headers_present_on_page_request(self, test_client):
        """Test that all expected headers are present on a page request."""
        client, _ = test_client
        response = await client.get("/recent")

        # Cache headers
        assert "cache-control" in response.headers

        # Security headers (HSTS and X-Content-Type-Options handled by Cloudflare)
        assert "x-frame-options" in response.headers
        assert "content-security-policy" in response.headers
        assert "referrer-policy" in response.headers
        assert "permissions-policy" in response.headers

    @pytest.mark.asyncio
    async def test_static_assets_get_correct_headers(self, test_client):
        """Test that static assets have cache but also security headers."""
        client, _ = test_client
        response = await client.get("/static/css/styles.css")

        # Should have long cache
        assert "max-age=31536000" in response.headers.get("cache-control", "")

        # But also security headers (X-Content-Type-Options handled by Cloudflare)
        assert response.headers.get("x-frame-options") == "DENY"

    @pytest.mark.asyncio
    async def test_api_endpoints_security_and_no_cache(self, test_client):
        """Test that API endpoints have both no-cache and security headers."""
        client, _ = test_client
        response = await client.get("/api/search/operations?q=read")

        # No caching for API
        assert response.headers.get("cache-control") == "no-store"

        # But still secure
        assert response.headers.get("x-frame-options") == "DENY"
        assert "noindex" in response.headers.get("x-robots-tag", "")


# =============================================================================
# HTTP Method Tests
# =============================================================================


class TestHTTPMethods:
    """Tests for proper HTTP method handling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method", ["DELETE", "PUT", "PATCH"])
    async def test_unsupported_methods_return_405(self, test_client, method):
        """Test that unsupported HTTP methods return 405, not 500."""
        client, _ = test_client
        response = await client.request(method, "/healthz")
        assert response.status_code == 405
        assert "Allow" in response.headers

    @pytest.mark.asyncio
    async def test_head_request_returns_200(self, test_client):
        """Test that HEAD requests work on health endpoints."""
        client, _ = test_client
        response = await client.head("/healthz")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_on_page_returns_405(self, test_client):
        """Test DELETE on page routes returns 405."""
        client, _ = test_client
        response = await client.delete("/recent")
        assert response.status_code == 405
