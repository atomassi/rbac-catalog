"""Tests for HTTP middleware functions."""

import pytest
from httpx import ASGITransport, AsyncClient

from rbaccatalog.web.middleware import (
    NEW_DOMAIN,
)


@pytest.fixture
async def test_client(async_session_maker):
    """Create a test client with in-memory database."""
    # Lazy import to avoid loading .env during test collection
    from rbaccatalog.web import app as app_module

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
        """Test that static assets get long cache headers on a 200 response.

        Requests a real asset shipped with the app so the route returns 200;
        the middleware now (correctly) sets ``no-store`` on 4xx/5xx responses
        so non-existent paths can no longer be used to assert this header.
        """
        client, _ = test_client
        response = await client.get("/robots.txt")
        assert response.status_code == 200
        cache_control = response.headers.get("cache-control", "")
        assert "max-age" in cache_control

    @pytest.mark.asyncio
    async def test_error_responses_are_not_long_cached(self, test_client):
        """Regression: 4xx/5xx must not inherit the page's long s-maxage TTL.

        A short negative cache on 404 is fine (~60 s); the bug being
        guarded is the CDN pinning a failure for the full detail-page
        ``s-maxage`` (1800 s) on every edge.
        """
        client, _ = test_client
        response = await client.get("/roles/test-role")
        assert response.status_code in (404, 400)
        cache_control = response.headers.get("cache-control", "")
        assert "s-maxage=1800" not in cache_control
        assert "max-age=31536000" not in cache_control

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
        """404 pages get a brief positive cache (absorb crawler storms).

        5xx responses must be ``no-store`` (failure amplification), but 404s
        for unknown URLs are safe to negatively cache for a short window so
        bot/crawler probes don't keep hitting the origin.
        """
        client, _ = test_client
        response = await client.get("/this-path-does-not-exist")
        assert response.status_code == 404
        cache_control = response.headers.get("cache-control", "")
        assert "max-age=60" in cache_control
        assert "no-store" not in cache_control


# =============================================================================
# Security Headers Tests
# =============================================================================


class TestSecurityHeaders:
    """Tests for add_security_headers middleware."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("header_name", "expected_value"),
        [
            pytest.param("x-frame-options", "DENY", id="x_frame_options"),
            pytest.param(
                "referrer-policy", "strict-origin-when-cross-origin", id="referrer_policy"
            ),
        ],
    )
    async def test_security_header_value(self, test_client, header_name: str, expected_value: str):
        """Test that security headers have correct values."""
        client, _ = test_client
        response = await client.get("/recent")
        assert response.headers.get(header_name) == expected_value

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
    @pytest.mark.parametrize(
        ("path", "should_have_noindex"),
        [
            pytest.param("/api/search/operations", True, id="api_endpoint"),
            pytest.param("/healthz", True, id="healthz"),
            pytest.param("/recent", False, id="regular_page"),
        ],
    )
    async def test_noindex_header(self, test_client, path: str, should_have_noindex: bool):
        """Test X-Robots-Tag noindex header on different endpoints."""
        client, _ = test_client
        response = await client.get(path)
        robots_tag = response.headers.get("x-robots-tag", "")
        if should_have_noindex:
            assert "noindex" in robots_tag
        else:
            assert "noindex" not in robots_tag


# =============================================================================
# Domain Redirect Tests
# =============================================================================


class TestDomainRedirect:
    """Tests for redirect_old_domain middleware."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("host_header", "header_name"),
        [
            pytest.param(
                "azurerbac-builtinroles.azurewebsites.net",
                "x-forwarded-host",
                id="old_appservice_domain",
            ),
            pytest.param(
                "azurerbac-builtinroles-atgjgtc9a9exbahe.z02.azurefd.net",
                "x-forwarded-host",
                id="old_frontdoor_domain",
            ),
            pytest.param(
                "azurerbac-builtinroles.azurewebsites.net:443",
                "host",
                id="old_domain_with_port",
            ),
            pytest.param(
                "azurerbac-builtinroles.azurewebsites.net, proxy.internal",
                "x-forwarded-host",
                id="old_domain_comma_separated",
            ),
        ],
    )
    async def test_old_domains_redirect(self, test_client, host_header: str, header_name: str):
        """Test that old domains redirect to new domain."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers={header_name: host_header},
            follow_redirects=False,
        )
        assert response.status_code == 301
        assert NEW_DOMAIN in response.headers.get("location", "")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "query", "expected_in_location"),
        [
            pytest.param("/roles/test-role", "", "/roles/test-role", id="preserves_path"),
            pytest.param("/roles", "page=2&limit=25", "page=2", id="preserves_query"),
        ],
    )
    async def test_redirect_preserves_url_parts(
        self, test_client, path: str, query: str, expected_in_location: str
    ):
        """Test that redirect preserves path and query string."""
        client, _ = test_client
        url = f"{path}?{query}" if query else path
        response = await client.get(
            url,
            headers={"x-forwarded-host": "azurerbac-builtinroles.azurewebsites.net"},
            follow_redirects=False,
        )
        assert response.status_code == 301
        location = response.headers.get("location", "")
        assert expected_in_location in location

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("headers",),
        [
            pytest.param({"x-forwarded-host": "rbac-catalog.dev"}, id="canonical_domain"),
            pytest.param({"host": "localhost:8000"}, id="localhost"),
            pytest.param({}, id="no_host_header"),
        ],
    )
    async def test_allowed_domains_no_redirect(self, test_client, headers: dict):
        """Test that canonical domain and localhost are not redirected."""
        client, _ = test_client
        response = await client.get(
            "/recent",
            headers=headers,
            follow_redirects=False,
        )
        assert response.status_code != 301


# =============================================================================
# Integration Tests
# =============================================================================


class TestRequestBodySizeLimit:
    """Tests for RequestBodySizeLimitMiddleware (DoS hardening)."""

    @pytest.mark.asyncio
    async def test_oversize_content_length_rejected_with_413(self, test_client):
        """A POST with Content-Length above the cap must be rejected fast."""
        from rbaccatalog.web.constants import MAX_REQUEST_BODY_BYTES

        client, _ = test_client
        # Send a small body but advertise a huge Content-Length
        oversize = MAX_REQUEST_BODY_BYTES + 1
        response = await client.post(
            "/api/recommend-roles",
            content=b'{"operations":[]}',
            headers={"content-length": str(oversize), "content-type": "application/json"},
        )
        assert response.status_code == 413

    @pytest.mark.asyncio
    async def test_invalid_content_length_rejected(self, test_client):
        """A malformed Content-Length header must be rejected with 400."""
        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            content=b'{"operations":[]}',
            headers={"content-length": "not-a-number", "content-type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_normal_request_passes(self, test_client):
        """Normal-sized POST bodies must pass through untouched."""
        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            json={"operations": [{"name": "Microsoft.Storage/storageAccounts/read"}]},
        )
        # Either 200, 422 (no cache yet), or 429 are acceptable here — the
        # important assertion is that the body-size middleware did not 413/400.
        assert response.status_code not in (400, 413)

    @pytest.mark.asyncio
    async def test_streaming_body_over_cap_rejected_with_413(self):
        """An oversized body without Content-Length must be cut off by the stream wrapper.

        This exercises the layer-2 (receive-wrapping) path that defends
        against missing/lying ``Content-Length`` and chunked uploads.
        Done as a pure ASGI test because httpx always sets Content-Length.
        """
        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        async def downstream(scope, receive, send):
            # Drain the body to force the wrapper to run
            while True:
                msg = await receive()
                if not msg.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        mw = RequestBodySizeLimitMiddleware(downstream, max_bytes=1024)
        scope = {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],  # NO content-length
        }

        # Send 3 chunks totalling 3 KB (over the 1 KB cap)
        sent_chunks = [
            {"type": "http.request", "body": b"x" * 500, "more_body": True},
            {"type": "http.request", "body": b"x" * 500, "more_body": True},
            {"type": "http.request", "body": b"x" * 2000, "more_body": False},
        ]

        async def receive():
            return sent_chunks.pop(0)

        responses = []

        async def send(message):
            responses.append(message)

        await mw(scope, receive, send)

        # First response should be our 413
        assert responses, "middleware sent no response"
        assert responses[0]["type"] == "http.response.start"
        assert responses[0]["status"] == 413
        body = b"".join(m.get("body", b"") for m in responses if m["type"] == "http.response.body")
        assert b"Request body too large" in body

    @pytest.mark.asyncio
    async def test_negative_content_length_rejected(self, test_client):
        """A negative Content-Length is malformed per RFC 9110 — must be 400."""
        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            content=b'{"operations":[]}',
            headers={"content-length": "-1", "content-type": "application/json"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_zero_content_length_passes(self, test_client):
        """An explicit Content-Length: 0 must not be blocked by the body cap."""
        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            content=b"",
            headers={"content-length": "0", "content-type": "application/json"},
        )
        # The downstream app may return 400/422 for an empty body, but the
        # response must NOT be our middleware's plain-text rejection.
        assert response.text != "Invalid Content-Length"
        assert response.text != "Request body too large"

    @pytest.mark.asyncio
    async def test_rejection_includes_connection_close(self, test_client):
        """413 responses must include ``Connection: close`` to prevent socket reuse abuse."""
        from rbaccatalog.web.constants import MAX_REQUEST_BODY_BYTES

        client, _ = test_client
        response = await client.post(
            "/api/recommend-roles",
            content=b'{"operations":[]}',
            headers={
                "content-length": str(MAX_REQUEST_BODY_BYTES + 1),
                "content-type": "application/json",
            },
        )
        assert response.status_code == 413
        assert response.headers.get("connection", "").lower() == "close"

    @pytest.mark.asyncio
    async def test_downstream_response_suppressed_after_413(self):
        """If downstream tries to respond after our 413, those messages must be dropped."""
        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        async def downstream(scope, receive, send):
            # Read until disconnect, then try to respond anyway (simulating
            # a handler that races with our 413).
            while True:
                msg = await receive()
                if msg["type"] == "http.disconnect" or not msg.get("more_body"):
                    break
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"downstream-ok"})

        mw = RequestBodySizeLimitMiddleware(downstream, max_bytes=100)
        scope = {"type": "http", "method": "POST", "headers": []}

        chunks = [{"type": "http.request", "body": b"x" * 500, "more_body": False}]

        async def receive():
            return chunks.pop(0)

        responses = []

        async def send(message):
            responses.append(message)

        await mw(scope, receive, send)

        # Only the 413 must be on the wire — downstream's 200 must be swallowed.
        starts = [m for m in responses if m["type"] == "http.response.start"]
        assert len(starts) == 1
        assert starts[0]["status"] == 413
        bodies = b"".join(
            m.get("body", b"") for m in responses if m["type"] == "http.response.body"
        )
        assert b"downstream-ok" not in bodies

    @pytest.mark.asyncio
    async def test_receive_returns_disconnect_after_cap(self):
        """After cap is exceeded, subsequent receive() calls must yield http.disconnect."""
        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        captured_messages = []

        async def downstream(scope, receive, send):
            # Read four times; first triggers cap, rest must all be disconnect.
            for _ in range(4):
                captured_messages.append(await receive())

        mw = RequestBodySizeLimitMiddleware(downstream, max_bytes=10)
        scope = {"type": "http", "method": "POST", "headers": []}
        chunks = [{"type": "http.request", "body": b"x" * 100, "more_body": False}]

        async def receive():
            return chunks.pop(0)

        async def send(message):
            pass

        await mw(scope, receive, send)

        assert captured_messages[0]["type"] == "http.disconnect"
        assert all(m["type"] == "http.disconnect" for m in captured_messages)

    @pytest.mark.asyncio
    async def test_cancellederror_propagates(self):
        """asyncio.CancelledError from downstream must NOT be swallowed.

        Swallowing CancelledError would break parent-task cancellation
        and prevent proper resource cleanup.
        """
        import asyncio

        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        async def downstream(scope, receive, send):
            raise asyncio.CancelledError

        mw = RequestBodySizeLimitMiddleware(downstream, max_bytes=1024)
        scope = {"type": "http", "method": "POST", "headers": []}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        with pytest.raises(asyncio.CancelledError):
            await mw(scope, receive, send)

    @pytest.mark.asyncio
    async def test_413_not_sent_after_response_already_started(self):
        """Streaming endpoints: if downstream started a response before the
        oversize chunk arrives, we MUST NOT emit a second http.response.start.

        Otherwise we'd commit a double-start ASGI protocol violation. The
        existing response wins; the connection just disconnects.
        """
        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        async def streaming_downstream(scope, receive, send):
            # Start responding BEFORE consuming the body.
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            # Now read body — first chunk will trip the cap.
            await receive()

        mw = RequestBodySizeLimitMiddleware(streaming_downstream, max_bytes=10)
        scope = {"type": "http", "method": "POST", "headers": []}
        chunks = [{"type": "http.request", "body": b"x" * 500, "more_body": False}]

        async def receive():
            return chunks.pop(0)

        responses = []

        async def send(message):
            responses.append(message)

        await mw(scope, receive, send)

        # Exactly one http.response.start, and it's the downstream 200 — NOT 413.
        starts = [m for m in responses if m["type"] == "http.response.start"]
        assert len(starts) == 1, f"expected single start, got {len(starts)}"
        assert starts[0]["status"] == 200

    @pytest.mark.asyncio
    async def test_none_body_does_not_crash(self):
        """A buggy server sending ``body=None`` (off-spec but possible) must
        not crash with TypeError — we defensively coerce to b''."""
        from rbaccatalog.web.middleware import RequestBodySizeLimitMiddleware

        captured = []

        async def downstream(scope, receive, send):
            captured.append(await receive())

        mw = RequestBodySizeLimitMiddleware(downstream, max_bytes=1024)
        scope = {"type": "http", "method": "POST", "headers": []}
        chunks = [{"type": "http.request", "body": None, "more_body": False}]

        async def receive():
            return chunks.pop(0)

        async def send(message):
            pass

        # Must not raise TypeError on len(None).
        await mw(scope, receive, send)
        assert captured[0]["type"] == "http.request"


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
        """Test that 200 static responses carry cache and security headers.

        Uses ``/robots.txt`` (always 200) because the previous probe of a
        missing CSS asset 404'd and is now (correctly) ``no-store``.
        """
        client, _ = test_client
        response = await client.get("/robots.txt")
        assert response.status_code == 200

        # Should have cache headers
        assert "max-age" in response.headers.get("cache-control", "")

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
