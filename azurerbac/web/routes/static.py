"""Static content routes."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response

from azurerbac.web.constants import SITE_URL
from azurerbac.web.utils import slugify

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
            "# Allow all bots",
            "User-agent: *",
            "Allow: /",
            "",
            "# Block API endpoints from crawling",
            "Disallow: /api/",
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
async def apple_touch_icon() -> Response:
    """Serve Apple touch icon (192x192)."""
    return _static_response(
        (_get_static_images_path() / "favicon-192.png").read_bytes(),
        "image/png",
    )


@router.get(
    "/sitemap.xml",
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}}},
)
async def sitemap_xml(request: Request) -> Response:
    """Generate dynamic sitemap with all role and operation pages."""
    # Get all data from cache - no database query needed
    app_cache = request.app.state.app_cache

    # Get all roles from cache (already sorted by name in cache)
    all_roles = app_cache.get_all_roles()
    roles = [(role.name, role.properties.role_name) for role in all_roles]
    roles.sort(key=lambda x: x[1].lower())  # Sort by role_name

    today = dt.datetime.now(dt.UTC).date().isoformat()

    urls = [
        # Home page - canonical URL, highest priority, updated daily
        # Note: /recent is an alias that redirects canonical to /, so not in sitemap
        f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>""",
        # Roles list page - high priority, updated daily
        f"""  <url>
    <loc>{SITE_URL}/roles</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.95</priority>
  </url>""",
        # Operations list page - high priority, updated weekly
        f"""  <url>
    <loc>{SITE_URL}/operations</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>""",
        # Role Recommender page - high value page
        f"""  <url>
    <loc>{SITE_URL}/recommend</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
  </url>""",
        # Analytics page - updated weekly
        f"""  <url>
    <loc>{SITE_URL}/analytics</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>""",
        # About page - informational
        f"""  <url>
    <loc>{SITE_URL}/about</loc>
    <lastmod>{today}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>""",
    ]

    for role_id, role_name in roles:
        slug = slugify(role_name)
        urls.append(
            f"""  <url>
    <loc>{SITE_URL}/roles/{role_id}/{slug}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>"""
        )

    # Add individual operation detail pages to sitemap
    # Get operations from cache
    all_operations = app_cache.get_all_operations()
    for op in all_operations:
        op_name = op.name
        if op_name:
            # URL encode the operation name for the sitemap (encode / as %2F)
            # This matches the canonical tag and how Googlebot crawls links
            encoded_name = quote(op_name, safe="")
            urls.append(
                f"""  <url>
    <loc>{SITE_URL}/operations/{encoded_name}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>"""
            )

    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    return Response(
        content=sitemap,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=86400"},  # Cache for 1 day
    )


@router.head("/")
async def head_root() -> Response:
    """Handle HEAD requests for Azure Front Door health probes."""
    return Response(status_code=200)
