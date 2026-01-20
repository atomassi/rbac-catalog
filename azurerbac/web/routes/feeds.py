"""RSS/Atom feed route handlers for changelog subscription."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from azurerbac.web.constants import DEFAULT_DAYS, MAX_DAYS, MAX_PAGE_SIZE
from azurerbac.web.dependencies import BaseDeps, get_api_deps
from azurerbac.web.services.feeds import (
    build_atom_feed,
    build_rss_feed,
    get_recent_events,
)

router = APIRouter(tags=["feeds"])


@router.get(
    "/feeds/changelog.atom",
    response_class=Response,
    responses={200: {"content": {"application/atom+xml": {}}}},
)
async def changelog_atom_feed(
    request: Request,
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> Response:
    """Atom 1.0 feed of recent role changes."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    events = get_recent_events(deps.app_cache, cutoff, limit)

    site_url = str(request.base_url).rstrip("/")
    updated = dt.datetime.now(dt.UTC)

    xml_content = build_atom_feed(events, site_url, updated)

    return Response(
        content=xml_content,
        media_type="application/atom+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get(
    "/feeds/changelog.rss",
    response_class=Response,
    responses={200: {"content": {"application/rss+xml": {}}}},
)
async def changelog_rss_feed(
    request: Request,
    deps: Annotated[BaseDeps, Depends(get_api_deps)],
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = DEFAULT_DAYS,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> Response:
    """RSS 2.0 feed of recent role changes."""
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=days)
    events = get_recent_events(deps.app_cache, cutoff, limit)

    site_url = str(request.base_url).rstrip("/")
    updated = dt.datetime.now(dt.UTC)

    xml_content = build_rss_feed(events, site_url, updated)

    return Response(
        content=xml_content,
        media_type="application/rss+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
