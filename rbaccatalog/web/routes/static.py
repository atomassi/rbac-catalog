"""Static content routes."""

from __future__ import annotations

import logging
from http import HTTPStatus
from pathlib import Path
from typing import Final

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import Response

from rbaccatalog.core.constants import SITE_URL

logger = logging.getLogger(__name__)

INDEXNOW_KEY: Final = "4484caab4dbc472ca61ac1141d812336"

_STATIC_IMAGES_DIR: Final = Path(__file__).parent.parent / "static" / "images"
_CACHE_1D: Final = {"Cache-Control": "public, max-age=86400"}

_FAVICON_BINARY: Final = (
    "favicon.ico",
    "favicon-48.png",
    "favicon-192.png",
    "apple-touch-icon.png",
)

# Populated during app startup via load_static_assets()
_STATIC_BYTES: dict[str, bytes] = {}
_STATIC_TEXT: dict[str, str] = {}


async def load_static_assets() -> None:
    """Load favicon assets during app startup with error handling.

    Missing files are logged as warnings; the app continues to serve
    other routes and returns 404 for absent assets.
    """
    for name in _FAVICON_BINARY:
        path = _STATIC_IMAGES_DIR / name
        try:
            _STATIC_BYTES[name] = await anyio.Path(path).read_bytes()
        except OSError:
            logger.warning("Static asset missing: %s", path)
    try:
        _STATIC_TEXT["favicon.svg"] = await anyio.Path(
            _STATIC_IMAGES_DIR / "favicon.svg"
        ).read_text(encoding="utf-8")
    except OSError:
        logger.warning("Static asset missing: %s/favicon.svg", _STATIC_IMAGES_DIR)


router = APIRouter(tags=["static"])


@router.get(
    "/robots.txt",
    response_class=Response,
    responses={200: {"content": {"text/plain": {}}}},
)
async def robots_txt() -> Response:
    """Serve robots.txt."""
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /roles/",
            "Allow: /operations/",
            "Allow: /compare/",
            "Allow: /recommend",
            "Allow: /analytics",
            "Allow: /about",
            "Disallow: /api/",
            "",
            "User-agent: SemrushBot",
            "User-agent: SemrushBot-BA",
            "User-agent: SemrushBot-SI",
            "User-agent: SemrushBot-SWA",
            "User-agent: SemrushBot-OCOB",
            "User-agent: SemrushBot-FT",
            "User-agent: SemrushBot-ESI",
            "User-agent: SiteAuditBot",
            "User-agent: SplitSignalBot",
            "User-agent: RyteBot",
            "User-agent: MJ12bot",
            "User-agent: AhrefsBot",
            "User-agent: DotBot",
            "Disallow: /",
            "",
            f"Sitemap: {SITE_URL}/sitemap.xml",
            "",
        ]
    )
    return Response(
        content=body,
        media_type="text/plain",
        headers=_CACHE_1D,
    )


@router.get("/googleec37c4d2676ac205.html")
async def google_site_verification() -> Response:
    """Google Search Console verification."""
    return Response(
        content="google-site-verification: googleec37c4d2676ac205.html",
        media_type="text/html",
        headers=_CACHE_1D,
    )


@router.get(f"/{INDEXNOW_KEY}.txt")
async def indexnow_key() -> Response:
    """IndexNow key verification."""
    return Response(
        content=INDEXNOW_KEY,
        media_type="text/plain",
        headers=_CACHE_1D,
    )


def _static_response(content: bytes | str, media_type: str) -> Response:
    return Response(content=content, media_type=media_type, headers=_CACHE_1D)


@router.get(
    "/favicon.ico",
    response_class=Response,
    responses={200: {"content": {"image/x-icon": {}}}},
)
async def favicon_ico() -> Response:
    """Serve favicon.ico."""
    if data := _STATIC_BYTES.get("favicon.ico"):
        return _static_response(data, "image/x-icon")
    return Response(status_code=HTTPStatus.NOT_FOUND)


@router.get(
    "/favicon.svg",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
)
async def favicon_svg() -> Response:
    """Serve favicon.svg."""
    if data := _STATIC_TEXT.get("favicon.svg"):
        return _static_response(data, "image/svg+xml")
    return Response(status_code=HTTPStatus.NOT_FOUND)


@router.get("/favicon-48.png")
async def favicon_png_48() -> Response:
    """Serve 48x48 favicon."""
    if data := _STATIC_BYTES.get("favicon-48.png"):
        return _static_response(data, "image/png")
    return Response(status_code=HTTPStatus.NOT_FOUND)


@router.get("/favicon-192.png")
async def favicon_png_192() -> Response:
    """Serve 192x192 favicon."""
    if data := _STATIC_BYTES.get("favicon-192.png"):
        return _static_response(data, "image/png")
    return Response(status_code=HTTPStatus.NOT_FOUND)


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon() -> Response:
    """Serve Apple touch icon."""
    if data := _STATIC_BYTES.get("apple-touch-icon.png"):
        return _static_response(data, "image/png")
    return Response(status_code=HTTPStatus.NOT_FOUND)


@router.api_route(
    "/sitemap.xml",
    methods=["GET", "HEAD"],
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}}},
)
async def sitemap_xml(request: Request) -> Response:
    """Return pre-built sitemap from cache."""
    app_cache = request.app.state.app_cache
    sitemap = app_cache.get_sitemap()

    if sitemap is None:
        # Fallback if cache not yet loaded (shouldn't happen in production)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            media_type="application/xml",
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            headers={"Retry-After": "60"},
        )

    # Format Last-Modified as HTTP-date (RFC 7231)
    last_modified = sitemap.built_at.strftime("%a, %d %b %Y %H:%M:%S GMT")

    return Response(
        content=sitemap.content,
        media_type="application/xml",
        headers={
            "Cache-Control": "public, max-age=86400",  # Cache for 1 day
            "Last-Modified": last_modified,
        },
    )
