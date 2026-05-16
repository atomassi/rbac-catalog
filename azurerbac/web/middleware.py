"""HTTP middleware."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from azurerbac.core.constants import NEW_DOMAIN
from azurerbac.web.constants import (
    CACHE_HEADER_DETAIL_PAGE,
    CACHE_HEADER_MAIN_PAGE,
    CACHE_HEADER_NONE,
    CACHE_HEADER_NOT_FOUND,
    CACHE_HEADER_STATIC,
    COOP_HEADER,
    CSP_HEADER,
    HEALTH_PATHS,
    MAX_REQUEST_BODY_BYTES,
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

    # API/health endpoints are always no-store regardless of status code
    # (must come before the error-status branch so 4xx API responses do
    # not inherit the short positive cache used for HTML 404 pages).
    if path.startswith("/api/") or path in HEALTH_PATHS:
        response.headers["Cache-Control"] = CACHE_HEADER_NONE
        return response

    # Error responses: differentiate to avoid failure amplification at the
    # CDN edge while still negatively caching cheap bot/crawler 404 storms.
    #   * 5xx           -> no-store (never pin a backend outage in the CDN)
    #   * 401/403/429   -> no-store (must re-evaluate auth/rate state)
    #   * 404/410       -> short positive cache (absorbs repeated bad URLs)
    #   * everything else 4xx -> no-store (safe default)
    if response.status_code >= 500:
        response.headers["Cache-Control"] = CACHE_HEADER_NONE
        return response
    if response.status_code in (404, 410):
        response.headers["Cache-Control"] = CACHE_HEADER_NOT_FOUND
        return response
    if response.status_code >= 400:
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


_BODYLESS_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "DELETE"})


class RequestBodySizeLimitMiddleware:
    """Pure ASGI middleware that caps inbound request body size.

    Starlette has no default body cap, so a malicious client can stream
    a multi-gigabyte body and exhaust memory/CPU before any handler runs.

    Implemented as a pure ASGI middleware (not ``BaseHTTPMiddleware``)
    because the latter buffers the body itself and would bypass any
    wrapping of the receive callable.

    Two layers of defence:

    1. Header check: short-circuit when ``Content-Length`` already
       exceeds the cap (no body bytes read).
    2. Streaming check: delegate to ``_BodyLimitResponder``, which
       counts bytes through a wrapped receive callable and sends 413
       directly when the cap is crossed.

    Follows Starlette's standard dispatcher + responder pattern
    (see ``starlette.middleware.gzip.GZipMiddleware``).
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in _BODYLESS_METHODS:
            await self.app(scope, receive, send)
            return

        if (status := self._check_content_length(scope)) is not None:
            await _send_plain(send, status, _STATUS_BODIES[status])
            return

        responder = _BodyLimitResponder(self.app, self.max_bytes, receive, send)
        await responder(scope)

    def _check_content_length(self, scope: Scope) -> int | None:
        """Return an HTTP status to short-circuit with, or ``None`` to proceed."""
        raw = Headers(scope=scope).get("content-length")
        if raw is None:
            return None
        try:
            declared = int(raw)
        except ValueError:
            return 400  # malformed header
        if declared < 0:
            return 400  # RFC 9110: Content-Length must be a non-negative integer
        if declared > self.max_bytes:
            return 413
        return None


class _BodyLimitResponder:
    """Per-request ASGI responder enforcing a body-size cap.

    Mirrors Starlette's ``GZipResponder`` pattern: bound methods are
    passed as the wrapped ``receive`` and ``send`` callables, so all
    state lives on ``self`` — no closures, no ``nonlocal``.

    When the cap is exceeded we:
      * send the 413 response directly on the original ``send`` ONLY if
        downstream has not yet emitted its own ``http.response.start``
        (otherwise we would commit a double-start ASGI protocol violation
        and just return ``http.disconnect`` instead),
      * return ``http.disconnect`` to downstream so handlers stop
        cleanly (raising would not propagate — Starlette's
        ``BaseHTTPMiddleware`` swallows ``Exception`` in its anyio
        task group and rewrites it as a 400/500 response),
      * silently drop any response downstream tries to emit afterwards
        so we don't violate the ASGI single-response rule.
    """

    __slots__ = (
        "app",
        "max_bytes",
        "receive",
        "received",
        "response_started",
        "send",
        "too_large",
    )

    def __init__(self, app: ASGIApp, max_bytes: int, receive: Receive, send: Send) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.receive = receive
        self.send = send
        self.received = 0
        self.too_large = False
        self.response_started = False

    async def __call__(self, scope: Scope) -> None:
        try:
            await self.app(scope, self.wrapped_receive, self.wrapped_send)
        except Exception:
            # Downstream may legitimately raise after seeing the
            # synthetic disconnect (e.g. FastAPI JSON parse failure on
            # truncated body). Once we've sent 413, those follow-on
            # errors are irrelevant. ``BaseException`` (CancelledError,
            # SystemExit, KeyboardInterrupt) is intentionally not
            # caught so task cancellation still propagates.
            if not self.too_large:
                raise

    async def wrapped_receive(self) -> Message:
        if self.too_large:
            return {"type": "http.disconnect"}
        message = await self.receive()
        if message["type"] == "http.request":
            # ``body`` should always be ``bytes`` per the ASGI spec, but
            # be defensive: a buggy server emitting ``None`` here would
            # otherwise crash with ``TypeError`` and surface a 500.
            self.received += len(message.get("body") or b"")
            if self.received > self.max_bytes:
                self.too_large = True
                if not self.response_started:
                    await _send_plain(self.send, 413, _STATUS_BODIES[413])
                return {"type": "http.disconnect"}
        return message

    async def wrapped_send(self, message: Message) -> None:
        if self.too_large:
            return
        if message["type"] == "http.response.start":
            self.response_started = True
        await self.send(message)


_STATUS_BODIES: Final[dict[int, bytes]] = {
    400: b"Invalid Content-Length",
    413: b"Request body too large",
}


async def _send_plain(send: Send, status: int, body: bytes) -> None:
    """Send a minimal plain-text ASGI response and signal connection close.

    Including ``connection: close`` prevents a malicious client from
    holding the keep-alive socket open and forcing uvicorn to keep
    draining further oversize bodies from the wire.
    """
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


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
