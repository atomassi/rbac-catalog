"""Analytics route handlers."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from azurerbac.web.dependencies import DashboardDeps, get_dashboard_deps
from azurerbac.web.services.analytics import (
    AnalyticsNotAvailableError,
    get_analytics_from_cache,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


@router.api_route("/analytics", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def analytics_dashboard(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
) -> HTMLResponse:
    """Render the analytics dashboard page with comprehensive statistics.

    Returns:
        Rendered ``analytics.html`` template populated with chart data.

    Raises:
        HTTPException: ``503 Service Unavailable`` when the analytics cache
            has not yet been built (e.g., during cold start).
    """
    logger.info("Analytics dashboard requested")

    try:
        analytics = get_analytics_from_cache()
    except AnalyticsNotAvailableError as exc:
        logger.warning("Analytics dashboard unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics are temporarily unavailable. Please try again later.",
        ) from exc

    # Convert daily changes to JSON-serializable format for Chart.js
    daily_chart_data = {
        "labels": [
            dc.date.isoformat() if isinstance(dc.date, dt.date) else str(dc.date)
            for dc in analytics.daily_changes
        ],
        "additions": [dc.additions for dc in analytics.daily_changes],
        "updates": [dc.updates for dc in analytics.daily_changes],
        "deletions": [dc.deletions for dc in analytics.daily_changes],
    }

    # Provider chart data (top 10 for cleaner visualization)
    top_10_providers = analytics.top_providers[:10]
    provider_chart_data = {
        "labels": [p.provider for p in top_10_providers],
        "values": [p.operation_count for p in top_10_providers],
    }

    context = {
        "tab": "analytics",
        "analytics": analytics,
        "daily_chart_data": daily_chart_data,
        "provider_chart_data": provider_chart_data,
        "total_roles": deps.app_cache.cache.active_roles_count,
    }

    return deps.templates.TemplateResponse(request, "analytics.html", context)
