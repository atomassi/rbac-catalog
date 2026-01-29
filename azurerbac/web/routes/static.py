"""Static content routes."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import Response

from azurerbac.web.constants import SITE_URL

INDEXNOW_KEY: Final = "4484caab4dbc472ca61ac1141d812336"

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
            "Disallow: /",
            "",
            f"Sitemap: {SITE_URL}/sitemap.xml",
            "",
        ]
    )
    return Response(
        content=body,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/googleec37c4d2676ac205.html")
async def google_site_verification() -> Response:
    """Google Search Console verification."""
    return Response(
        content="google-site-verification: googleec37c4d2676ac205.html",
        media_type="text/html",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get(f"/{INDEXNOW_KEY}.txt")
async def indexnow_key() -> Response:
    """IndexNow key verification."""
    return Response(
        content=INDEXNOW_KEY,
        media_type="text/plain",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _get_static_images_path() -> Path:
    """Get static/images directory path."""
    return Path(__file__).parent.parent / "static" / "images"


def _static_response(content: bytes | str, media_type: str) -> Response:
    """Create cached static response."""
    return Response(
        content=content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get(
    "/favicon.ico",
    response_class=Response,
    responses={200: {"content": {"image/x-icon": {}}}},
)
async def favicon_ico() -> Response:
    """Serve favicon.ico."""
    return _static_response(
        (_get_static_images_path() / "favicon.ico").read_bytes(),
        "image/x-icon",
    )


@router.get(
    "/favicon.svg",
    response_class=Response,
    responses={200: {"content": {"image/svg+xml": {}}}},
)
async def favicon_svg() -> Response:
    """Serve favicon.svg."""
    return _static_response(
        (_get_static_images_path() / "favicon.svg").read_text(),
        "image/svg+xml",
    )


@router.get("/favicon-48.png")
async def favicon_png_48() -> Response:
    """Serve 48x48 PNG favicon."""
    return _static_response(
        (_get_static_images_path() / "favicon-48.png").read_bytes(),
        "image/png",
    )


@router.get("/favicon-192.png")
async def favicon_png_192() -> Response:
    """Serve 192x192 PNG favicon for Android/PWA."""
    return _static_response(
        (_get_static_images_path() / "favicon-192.png").read_bytes(),
        "image/png",
    )


@router.get("/apple-touch-icon.png")
@router.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon() -> Response:
    """Serve Apple touch icon (180x180)."""
    return _static_response(
        (_get_static_images_path() / "apple-touch-icon.png").read_bytes(),
        "image/png",
    )


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
