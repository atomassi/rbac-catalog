"""HTTP middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from azurerbac.web.constants import (
    CACHE_HEADER_DETAIL_PAGE,
    CACHE_HEADER_MAIN_PAGE,
    CACHE_HEADER_NONE,
    CACHE_HEADER_STATIC,
    COOP_HEADER,
    CSP_HEADER,
    HEALTH_PATHS,
    NEW_DOMAIN,
    OLD_DOMAINS,
    PERMISSIONS_POLICY_HEADER,
    VARY_ENCODING,
)

logger = logging.getLogger(__name__)

_MAIN_PAGES: Final = frozenset({"/", "/recent", "/roles", "/operations", "/recommend", "/about"})


async def add_cache_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Add cache headers for CDN and browser caching."""
    response = await call_next(request)
    path = request.url.path

    # Skip caching for API/health endpoints
    if path.startswith("/api/") or path in HEALTH_PATHS:
        response.headers["Cache-Control"] = CACHE_HEADER_NONE
        return response

    # Static assets - long cache (1 year, immutable)
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = CACHE_HEADER_STATIC
        response.headers["Vary"] = VARY_ENCODING
        return response

    # Main list pages
    if path in _MAIN_PAGES:
        response.headers["Cache-Control"] = CACHE_HEADER_MAIN_PAGE
        response.headers["Vary"] = VARY_ENCODING
        return response

    # Role/operation detail pages - cache longer
    if path.startswith(("/roles/", "/operations/")):
        response.headers["Cache-Control"] = CACHE_HEADER_DETAIL_PAGE
        response.headers["Vary"] = VARY_ENCODING
        return response

    # For other HTML pages, moderate caching
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = CACHE_HEADER_MAIN_PAGE
        response.headers["Vary"] = VARY_ENCODING

    return response


async def add_security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Add security headers."""
    response = await call_next(request)
    headers = response.headers
    path = request.url.path

    # Prevent indexing of non-content endpoints
    if path.startswith("/api/") or path in HEALTH_PATHS:
        headers["X-Robots-Tag"] = "noindex, nofollow, nosnippet"

    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    headers["X-XSS-Protection"] = "1; mode=block"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Content-Security-Policy"] = CSP_HEADER
    headers["Cross-Origin-Opener-Policy"] = COOP_HEADER
    headers["Permissions-Policy"] = PERMISSIONS_POLICY_HEADER

    return response


async def redirect_old_domain(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """301 redirect from old domains to canonical domain."""
    # Never redirect health checks - probes may use any host
    if request.url.path in HEALTH_PATHS:
        return await call_next(request)

    host = request.headers.get("x-forwarded-host", request.headers.get("host", "")).lower()

    if any(old in host for old in OLD_DOMAINS):
        new_url = f"https://{NEW_DOMAIN}{request.url.path}"
        if request.url.query:
            new_url += f"?{request.url.query}"

        return RedirectResponse(url=new_url, status_code=301)

    return await call_next(request)
