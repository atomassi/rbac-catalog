"""HTTP middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from azurerbac.core.constants import NEW_DOMAIN
from azurerbac.web.constants import (
    CACHE_HEADER_DETAIL_PAGE,
    CACHE_HEADER_MAIN_PAGE,
    CACHE_HEADER_NONE,
    CACHE_HEADER_STATIC,
    COOP_HEADER,
    CSP_HEADER,
    HEALTH_PATHS,
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

    raw_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if not raw_host:
        return await call_next(request)

    # Proxy chain first entry, then strip port
    client_host = raw_host.split(",", 1)[0].strip().lower()
    domain = client_host.partition(":")[0]

    if domain in OLD_DOMAINS:
        new_url = request.url.replace(scheme="https", netloc=NEW_DOMAIN)
        return RedirectResponse(url=str(new_url), status_code=301)

    return await call_next(request)
