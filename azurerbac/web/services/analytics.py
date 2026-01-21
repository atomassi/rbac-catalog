"""Analytics service helpers for web layer."""

from __future__ import annotations

from fastapi import HTTPException, status

from azurerbac.analytics.models import AnalyticsData
from azurerbac.cache import get_cache_service


def get_analytics_from_cache() -> AnalyticsData:
    """Get analytics data from cache.

    Raises:
        HTTPException: 503 Service Unavailable if analytics data is not in cache.
            This indicates the cache was not properly initialized at startup.
    """
    cache = get_cache_service().container.cache
    if cache.analytics is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analytics data not available. Cache may not be fully initialized.",
        )
    return cache.analytics
