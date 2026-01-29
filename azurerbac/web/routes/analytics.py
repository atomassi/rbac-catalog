"""Analytics route handlers."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from azurerbac.web.dependencies import DashboardDeps, get_dashboard_deps
from azurerbac.web.services.analytics import get_analytics_from_cache

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


@router.api_route("/analytics", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def analytics_dashboard(
    request: Request,
    deps: Annotated[DashboardDeps, Depends(get_dashboard_deps)],
) -> HTMLResponse:
    """Analytics dashboard page with comprehensive statistics."""
    logger.info("Analytics dashboard requested")

    # Preserve ai=1 parameter if set
    ai_mode = request.query_params.get("ai") == "1"

    analytics = get_analytics_from_cache()

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
        "ai_mode": ai_mode,
    }

    return deps.templates.TemplateResponse(request, "analytics.html", context)
